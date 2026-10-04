"""Tests for resetting the steps of a stage to their defaults: what each scope removes, what stays, and how it is undone."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import frozen
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor, PageStepState
from bookreviver.domain.enums import ChangeSource, EditorKind, ResetScope, Stage, StageState, StepLayer
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import PageId, StepId
from bookreviver.domain.values import NewPageEdit, PageStageKey, PageStepKey, RecipeDraft, ResetRequest, StageRun, Step
from tests.helpers.builders import new_account_id
from tests.helpers.page_batches import step_resets
from tests.helpers.processors import STRENGTH_PARAMETER, FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRONGER: int = 2
ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))


@frozen
class WorkedBook:
    """A book of three pages whose recipe has two steps, and the work its pages have for them.

    The first page changes the strength of both steps and has an edit of the first, the second page changes the
    strength of the first step, and the third page has an edit of the second step and changes no field.

    :ivar actor: Account owning the book.
    :ivar project: The book.
    :ivar pages: The pages in book order.
    :ivar first: The first step of the recipe.
    :ivar second: The second step of the recipe.
    """

    actor: Actor
    project: Project
    pages: list[Page]
    first: StepId
    second: StepId

    def key(self, page_index: int, step: StepId) -> PageStepKey:
        """Give the key of a step on a page of the book.

        :param page_index: Index of the page in book order.
        :type page_index: int
        :param step: The step.
        :type step: StepId
        :returns: The key.
        :rtype: PageStepKey
        """
        return PageStepKey(self.pages[page_index].id, Stage.GEOMETRY, step)


async def worked_book(kit: ProcessingKit) -> WorkedBook:
    """Seed the book of a worked recipe with two steps and the settings and the edits of its pages.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The book.
    :rtype: WorkedBook
    """
    actor, project = await kit.seed_project()
    pages = []
    for order_key in ORDER_KEYS:
        page, _ = await kit.seed_scan_page(project, order_key=order_key)
        await kit.seed_base_version(page)
        pages.append(page)
    draft = RecipeDraft(
        name='Two steps',
        steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 1}) for _ in range(2)],
    )
    saved = await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, draft)
    book = WorkedBook(actor, project, pages, saved.steps[0].step_id, saved.steps[1].step_id)
    settings, edits = kit.page_settings(), kit.edits()
    await settings.change(actor, project.id, book.key(0, book.first), STRENGTH_PARAMETER, STRONGER)
    await settings.change(actor, project.id, book.key(0, book.second), STRENGTH_PARAMETER, STRONGER)
    await settings.change(actor, project.id, book.key(1, book.first), STRENGTH_PARAMETER, STRONGER)
    await edits.save(actor, project.id, book.key(0, book.first), ROTATION, None)
    await edits.save(actor, project.id, book.key(2, book.second), ROTATION, None)
    return book


async def stored(kit: ProcessingKit, key: PageStepKey) -> PageStepState | None:
    """Read the state of a step on a page as it is stored.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The state, or None when the page has neither a setting nor an edit for the step.
    :rtype: PageStepState | None
    """
    return await kit.uow().page_step_states.find(key)


def signature(state: PageStepState | None) -> tuple[dict[str, object], str] | None:
    """Reduce a state to what a reset takes and an undo gives back: the fields and the hash of the edit.

    :param state: The state, or None for a step that holds nothing.
    :type state: PageStepState | None
    :returns: The fields the page changes and the hash of its edit, which is empty without one, or None.
    :rtype: tuple[dict[str, object], str] | None
    """
    if state is None:
        return None
    return dict(state.params), '' if state.edit is None else state.edit.edit_hash


async def held(kit: ProcessingKit, book: WorkedBook) -> list[tuple[bool, bool]]:
    """Tell for each step on each page of the book whether it holds a setting and whether it holds an edit.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The book.
    :type book: WorkedBook
    :returns: One pair for each page and each step, the first step first, in book order.
    :rtype: list[tuple[bool, bool]]
    """
    found = []
    for index in range(len(book.pages)):
        for step in (book.first, book.second):
            state = await stored(kit, book.key(index, step))
            found.append((state is not None and bool(state.params), state is not None and state.edit is not None))
    return found


def reset(book: WorkedBook, scope: ResetScope, *, page: int = 0, step: StepId | None = None) -> ResetRequest:
    """Ask for a confirmed reset of the book.

    :param book: The book.
    :type book: WorkedBook
    :param scope: The scope of the reset.
    :type scope: ResetScope
    :param page: Index of the open page.
    :type page: int
    :param step: The step, or None for the first.
    :type step: StepId | None
    :returns: The request, which carries the confirmation.
    :rtype: ResetRequest
    """
    return ResetRequest(
        stage=Stage.GEOMETRY,
        scope=scope,
        page_id=book.pages[page].id,
        step_id=book.first if step is None else step,
        confirm=True,
    )


# Page by page, the first step then the second: whether each holds a setting and whether each holds an edit
WORKED: list[tuple[bool, bool]] = [
    (True, True),
    (True, False),
    (True, False),
    (False, False),
    (False, False),
    (False, True),
]


class TestRequest:
    """Tests for what a request names."""

    @pytest.mark.parametrize('scope', [ResetScope.PAGE_STEP, ResetScope.PAGE], ids=['page-step', 'page'])
    def test_a_scope_of_one_page_needs_the_page(self, scope: ResetScope) -> None:
        """Verify a scope that goes over the open page is refused without it, even when it names a step.

        :param scope: Scope under test.
        :type scope: ResetScope
        """
        with pytest.raises(ValueError, match=r'names the page'):
            ResetRequest(stage=Stage.GEOMETRY, scope=scope, step_id=StepId(uuid4()))

    @pytest.mark.parametrize('scope', [ResetScope.PAGE_STEP, ResetScope.STEP], ids=['page-step', 'step'])
    def test_a_scope_of_one_step_needs_the_step(self, scope: ResetScope) -> None:
        """Verify a scope that goes over one step is refused without it, even when it names a page.

        :param scope: Scope under test.
        :type scope: ResetScope
        """
        with pytest.raises(ValueError, match=r'names the step'):
            ResetRequest(stage=Stage.GEOMETRY, scope=scope, page_id=PageId(uuid4()))

    def test_the_stage_scope_names_neither_a_page_nor_a_step(self) -> None:
        """Verify a reset of the whole stage is a valid request without either, and reaches other pages."""
        request = ResetRequest(stage=Stage.GEOMETRY, scope=ResetScope.STAGE)
        expect(request.open_page is None and request.chosen_step is None)
        expect(request.reaches_other_pages)
        assert_expectations()

    def test_a_page_the_scope_ignores_is_not_the_open_page(self) -> None:
        """Verify a page named for a scope of every page is ignored, as the scopes of a step ignore a step."""
        request = ResetRequest(
            stage=Stage.GEOMETRY, scope=ResetScope.STEP, page_id=PageId(uuid4()), step_id=StepId(uuid4())
        )
        expect(request.open_page is None and request.chosen_step == request.step_id)
        assert_expectations()


class TestImpact:
    """Tests for the count of the pages a reset would take work from."""

    @pytest.mark.parametrize(
        ('scope', 'step_number', 'counted'),
        [
            (ResetScope.PAGE_STEP, 0, (1, 1, 1)),
            (ResetScope.PAGE_STEP, 1, (0, 1, 1)),
            (ResetScope.PAGE, 0, (1, 1, 1)),
            (ResetScope.STEP, 0, (1, 2, 2)),
            (ResetScope.STEP, 1, (1, 1, 2)),
            (ResetScope.STAGE, 0, (2, 2, 3)),
        ],
        ids=['page-step', 'page-second-step', 'page', 'step', 'second-step', 'stage'],
    )
    async def test_each_scope_counts_the_pages_it_takes_work_from(
        self, fx_kit: ProcessingKit, scope: ResetScope, step_number: int, counted: tuple[int, int, int]
    ) -> None:
        """Verify the pages with an edit, the pages with a setting and the pages that lose either are counted.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param scope: Scope under test.
        :type scope: ResetScope
        :param step_number: Which step is named, the first being zero.
        :type step_number: int
        :param counted: The pages with an edit, with a setting, and the pages that lose work.
        :type counted: tuple[int, int, int]
        """
        book = await worked_book(fx_kit)
        request = reset(book, scope, step=(book.first, book.second)[step_number])
        impact = await step_resets(fx_kit).impact(book.actor, book.project.id, request)
        expect((impact.hand_pages, impact.settings_pages, impact.affected) == counted)
        expect(impact.scope is scope)
        assert_expectations()

    async def test_counting_changes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify the count writes no state and no history.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        written = await fx_kit.uow().page_step_changes.list_for_page(book.pages[0].id, Stage.GEOMETRY)
        await step_resets(fx_kit).impact(book.actor, book.project.id, reset(book, ResetScope.STAGE))
        expect(await held(fx_kit, book) == WORKED)
        expect(
            len(await fx_kit.uow().page_step_changes.list_for_page(book.pages[0].id, Stage.GEOMETRY)) == len(written)
        )
        assert_expectations()

    async def test_a_stranger_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the count for a project of another account is refused like a missing project.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await step_resets(fx_kit).impact(stranger, book.project.id, reset(book, ResetScope.STAGE))


class TestPageStep:
    """Tests for the reset of one step on the open page."""

    async def test_the_setting_and_the_edit_of_the_step_go_and_nothing_else(self, fx_kit: ProcessingKit) -> None:
        """Verify the first step of the first page loses both layers, and every other state is as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.PAGE_STEP))
        expect(await held(fx_kit, book) == [(False, False), *WORKED[1:]])
        expect(
            [(change.layer, change.after) for change in done.changes]
            == [(StepLayer.SETTINGS, None), (StepLayer.HAND, None)]
        )
        assert_expectations()

    async def test_a_state_with_only_a_setting_loses_only_that_layer(self, fx_kit: ProcessingKit) -> None:
        """Verify a step that has no edit writes one change, and a step that has nothing writes none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        only_setting = await step_resets(fx_kit).reset(
            book.actor, book.project.id, reset(book, ResetScope.PAGE_STEP, page=1)
        )
        nothing = await step_resets(fx_kit).reset(
            book.actor, book.project.id, reset(book, ResetScope.PAGE_STEP, page=1, step=book.second)
        )
        expect([change.layer for change in only_setting.changes] == [StepLayer.SETTINGS])
        expect(nothing.changes == ())
        assert_expectations()

    async def test_the_changes_are_from_a_reset_in_one_batch_and_one_undo_gives_both_layers_back(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the changes name a reset and share a batch, and one undo restores the setting and the edit.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        key = book.key(0, book.first)
        before = await stored(fx_kit, key)
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.PAGE_STEP))
        undone = await fx_kit.page_history().undo(book.actor, book.project.id, key, None)
        after = await stored(fx_kit, key)
        expect(all(change.source is ChangeSource.RESET for change in done.changes))
        expect({change.batch_id for change in done.changes} == {done.batch_id})
        expect(len(undone) == len(done.changes))
        expect(signature(after) == signature(before) and before is not None)
        assert_expectations()

    async def test_the_open_page_needs_no_confirmation(self, fx_kit: ProcessingKit) -> None:
        """Verify a reset that goes over the open page alone is done without the confirmation.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        request = ResetRequest(
            stage=Stage.GEOMETRY, scope=ResetScope.PAGE_STEP, page_id=book.pages[0].id, step_id=book.first
        )
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, request)
        assert len(done.changes) == 2


class TestPage:
    """Tests for the reset of every step of the stage on the open page."""

    async def test_every_step_of_the_page_goes_and_the_other_pages_stay(self, fx_kit: ProcessingKit) -> None:
        """Verify both steps of the first page are reset, one batch holds all three layers, and the rest stays.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.PAGE))
        expect(await held(fx_kit, book) == [(False, False), (False, False), *WORKED[2:]])
        expect(len(done.changes) == 3)
        expect({change.page_id for change in done.changes} == {book.pages[0].id})
        assert_expectations()

    async def test_one_undo_of_a_step_gives_back_every_step_of_the_page(self, fx_kit: ProcessingKit) -> None:
        """Verify the undo of the second step brings the first step back too, since they share a batch.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.PAGE))
        await fx_kit.page_history().undo(book.actor, book.project.id, book.key(0, book.second), None)
        assert await held(fx_kit, book) == WORKED


class TestStep:
    """Tests for the reset of one step on every page."""

    async def test_the_step_goes_on_every_page_and_the_other_step_stays(self, fx_kit: ProcessingKit) -> None:
        """Verify the first step loses its settings and its edit everywhere, and the second step is as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STEP))
        expect(
            await held(fx_kit, book)
            == [(False, False), WORKED[1], (False, False), WORKED[3], (False, False), WORKED[5]]
        )
        assert_expectations()

    async def test_it_is_refused_until_it_is_confirmed_and_names_the_pages(self, fx_kit: ProcessingKit) -> None:
        """Verify an unconfirmed reset is a conflict that names the two pages and changes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        request = ResetRequest(
            stage=Stage.GEOMETRY, scope=ResetScope.STEP, page_id=book.pages[0].id, step_id=book.first
        )
        with pytest.raises(ConflictError, match=r'takes the work of 2 pages away'):
            await step_resets(fx_kit).reset(book.actor, book.project.id, request)
        assert await held(fx_kit, book) == WORKED

    async def test_a_step_without_work_needs_no_confirmation(self, fx_kit: ProcessingKit) -> None:
        """Verify a reset that would take work from no page is done without the confirmation.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STEP))
        request = ResetRequest(stage=Stage.GEOMETRY, scope=ResetScope.STEP, step_id=book.first)
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, request)
        assert done.changes == ()

    async def test_a_step_that_no_recipe_has_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a step the recipes of the stage do not have is refused, as the settings of a page refuse it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        with pytest.raises(NotFoundError):
            await step_resets(fx_kit).reset(
                book.actor, book.project.id, reset(book, ResetScope.STEP, step=StepId(uuid4()))
            )


class TestStage:
    """Tests for the reset of every step of the stage on every page."""

    async def test_every_state_of_the_stage_goes(self, fx_kit: ProcessingKit) -> None:
        """Verify no setting and no edit is left, and no stored state either.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STAGE))
        expect(await held(fx_kit, book) == [(False, False)] * 6)
        expect(len(done.changes) == 5)
        assert_expectations()

    async def test_it_is_refused_until_it_is_confirmed_and_names_the_pages(self, fx_kit: ProcessingKit) -> None:
        """Verify an unconfirmed reset is a conflict that names the three pages that have work.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        request = ResetRequest(stage=Stage.GEOMETRY, scope=ResetScope.STAGE)
        with pytest.raises(ConflictError, match=r'takes the work of 3 pages away'):
            await step_resets(fx_kit).reset(book.actor, book.project.id, request)
        assert await held(fx_kit, book) == WORKED

    async def test_one_undo_gives_the_work_back_on_every_page(self, fx_kit: ProcessingKit) -> None:
        """Verify the changes on three pages share a batch, and one undo restores every setting and every edit.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        before = [
            await stored(fx_kit, book.key(index, step)) for index in range(3) for step in (book.first, book.second)
        ]
        done = await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STAGE))
        undone = await fx_kit.page_history().undo(book.actor, book.project.id, book.key(1, book.first), None)
        after = [
            await stored(fx_kit, book.key(index, step)) for index in range(3) for step in (book.first, book.second)
        ]
        expect(len({change.batch_id for change in done.changes}) == 1)
        expect(sorted({change.page_id for change in undone}) == sorted(page.id for page in book.pages))
        expect([signature(state) for state in after] == [signature(state) for state in before])
        assert_expectations()

    async def test_the_recipe_is_the_default_and_stays_as_it_is(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps of the recipe keep their parameters, which are what the pages return to.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        before = await fx_kit.parts(fx_kit.uow()).recipes.active(book.project.id, Stage.GEOMETRY)
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STAGE))
        after = await fx_kit.parts(fx_kit.uow()).recipes.active(book.project.id, Stage.GEOMETRY)
        assert after.steps == before.steps

    async def test_a_state_of_a_step_that_no_recipe_has_is_left(self, fx_kit: ProcessingKit) -> None:
        """Verify a state kept for a step that left the recipes is not part of the stage any more, and stays.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        orphan = PageStepState(
            page_id=book.pages[0].id,
            stage=Stage.GEOMETRY,
            step_id=StepId(uuid4()),
            params={STRENGTH_PARAMETER: STRONGER},
            updated_at=fx_kit.clock.now(),
        )
        uow = fx_kit.uow()
        await uow.page_step_states.save(orphan)
        await uow.commit()
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STAGE))
        assert await stored(fx_kit, orphan.key) is not None

    async def test_a_stranger_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the reset of a project of another account is refused like a missing project.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await step_resets(fx_kit).reset(stranger, book.project.id, reset(book, ResetScope.STAGE))
        assert await held(fx_kit, book) == WORKED


class TestStale:
    """Tests for the stage of a page, which a reset marks stale where it took something."""

    async def test_the_pages_that_lost_work_go_stale_and_the_others_stay_fresh(self, fx_kit: ProcessingKit) -> None:
        """Verify a reset of the second step marks the two pages that had work on it, and not the page that had none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY)
        job = await fx_kit.service().start_run(book.actor, book.project.id, Stage.GEOMETRY, run)
        await fx_kit.jobs().run_stage(job.id)
        await fx_kit.work_queue()
        await step_resets(fx_kit).reset(book.actor, book.project.id, reset(book, ResetScope.STEP, step=book.second))
        states = [
            (await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in book.pages
        ]
        assert states == [StageState.STALE, StageState.FRESH, StageState.STALE]

    async def test_a_reset_that_takes_nothing_marks_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify a reset of a step the open page has no work on leaves its stage fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        book = await worked_book(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY)
        job = await fx_kit.service().start_run(book.actor, book.project.id, Stage.GEOMETRY, run)
        await fx_kit.jobs().run_stage(job.id)
        await fx_kit.work_queue()
        await step_resets(fx_kit).reset(
            book.actor, book.project.id, reset(book, ResetScope.PAGE_STEP, page=1, step=book.second)
        )
        record = await fx_kit.uow().page_stages.get(PageStageKey(book.pages[1].id, Stage.GEOMETRY))
        assert record.state is StageState.FRESH
