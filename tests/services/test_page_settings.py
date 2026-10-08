"""Tests for the values a step has for pages, the odd pages, the even pages and groups, and for the run that lays them."""

from typing import TYPE_CHECKING
from unittest.mock import patch
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory.unit_of_work import InMemoryPageRepository
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import (
    ChangeSource,
    EditorKind,
    JobState,
    OrderMode,
    RecipeKind,
    Stage,
    StageState,
    StepLayer,
    ValueScope,
    VersionScale,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import RecipeId, StepId
from bookreviver.domain.step_values import ValueField, ValueTarget
from bookreviver.domain.values import NewPageEdit, PageStageKey, PageStepKey, RecipeDraft, SliceRequest, StepPreview
from tests.helpers.builders import new_account_id
from tests.helpers.page_batches import PageValues
from tests.helpers.processors import FAILING_PARAMETER, RAN_KEY, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import head_of
from tests.services.test_processing_remake import run_geometry
from tests.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from bookreviver.domain.entities import Page, PageVersion, Project
    from bookreviver.domain.values import Step
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

COUNT_BEFORE_PATCH: str = 'bookreviver.adapters.persistence.memory.unit_of_work.InMemoryPageRepository.count_before'
STEPS_OF_A_CHAIN: int = 3
FAKE_KEY: str = FakeProcessor.spec.key
STRONGER: int = 2
STRONGEST: int = 3
OWN: int = 4
RECIPE_STRENGTH: int = 1
GROUP: str = 'Index'
NOT_FAILING: bool = False
OTHER_ORDER_KEY: str = 'a1'
EVERYTHING: SliceRequest = SliceRequest(limit=100)
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))


async def step_of(kit: ProcessingKit, project: Project) -> Step:
    """Read the step of the active geometry recipe of the book, which runs the fake processor.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The book.
    :type project: Project
    :returns: The first step of the recipe.
    :rtype: Step
    """
    return (await kit.parts(kit.uow()).recipes.of_kind(project.id, Stage.GEOMETRY, RecipeKind.TEXT)).steps[0]


async def ran_book(kit: ProcessingKit, *, count: int = 4) -> tuple[Actor, Project, list[Page], StepId]:
    """Seed a book of text pages in order, run the geometry stage on all of them, and find its step.

    The pages are at the places 1 to ``count`` of the book, so the first is an odd page and the second an even one.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param count: How many pages to seed.
    :type count: int
    :returns: The actor, the project, the pages and the identifier of the step of the geometry recipe.
    :rtype: tuple[Actor, Project, list[Page], StepId]
    """
    actor, project = await kit.seed_project()
    pages = [(await kit.seed_scan_page(project, order_key=f'a{number}'))[0] for number in range(count)]
    for page in pages:
        await kit.seed_base_version(page)
    await run_geometry(kit, actor, project)
    return actor, project, pages, (await step_of(kit, project)).step_id


def strength_field(step_id: StepId, target: ValueTarget) -> ValueField:
    """Name the strength of the step together with the pages a value of it is for.

    :param step_id: The step of the geometry recipe.
    :type step_id: StepId
    :param target: The pages.
    :type target: ValueTarget
    :returns: The field.
    :rtype: ValueField
    """
    return ValueField(stage=Stage.GEOMETRY, step_id=step_id, name=STRENGTH_PARAMETER, target=target)


def on(scope: ValueScope, *pages: Page, label: str = '') -> ValueTarget:
    """Name the pages a value is for.

    :param scope: The kind of part of the pages.
    :type scope: ValueScope
    :param pages: The pages, for the scope of pages.
    :type pages: Page
    :param label: Label of the group, for the scope of a group.
    :type label: str
    :returns: The target.
    :rtype: ValueTarget
    """
    return ValueTarget(scope=scope, page_ids=tuple(page.id for page in pages), group_label=label)


async def put_in_group(kit: ProcessingKit, page: Page, label: str) -> None:
    """Put a page in a group, as the user does by hand.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param label: Label of the group.
    :type label: str
    """
    uow = kit.uow()
    await uow.pages.update(evolve(await uow.pages.get(page.id), group_label=label))
    await uow.commit()


async def strengths(kit: ProcessingKit, actor: Actor, project: Project, pages: list[Page]) -> list[object]:
    """Read the strength each page runs the step with, as the service lists it, which is the recipe's for no value.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Owner of the book.
    :type actor: Actor
    :param project: The book.
    :type project: Project
    :param pages: The pages.
    :type pages: list[Page]
    :returns: The strength of each page.
    :rtype: list[object]
    """
    found: list[object] = []
    for page in pages:
        listed = await kit.page_settings().list(actor, project.id, page.id, Stage.GEOMETRY)
        found.append(listed[0].effective[STRENGTH_PARAMETER] if listed else RECIPE_STRENGTH)
    return found


async def stages_of(kit: ProcessingKit, pages: list[Page]) -> list[StageState]:
    """Read the state of the geometry stage of each page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param pages: The pages.
    :type pages: list[Page]
    :returns: The states.
    :rtype: list[StageState]
    """
    return [(await kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in pages]


class TestPageValues:
    """Tests for the values one page, or the pages the user selected, use for a field of a step."""

    async def test_field_is_stored_for_the_page_alone_and_marks_the_stage_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a set field is listed for its page only, the stage goes stale and nothing is computed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        runs = fx_kit.fake.runs
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        [listed] = await fx_kit.page_settings().list(actor, project.id, page.id, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(listed.params == {STRENGTH_PARAMETER: STRONGER})
        expect(listed.effective[STRENGTH_PARAMETER] == STRONGER)
        expect(listed.parts == [])
        expect(record.state is StageState.STALE)
        expect(fx_kit.fake.runs == runs)
        assert_expectations()

    async def test_second_field_joins_the_first_and_the_history_keeps_both_layers(self, fx_kit: ProcessingKit) -> None:
        """Verify a second field is added to the first, and each change is written with the layer before and after.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        values = PageValues(fx_kit, actor, project.id)
        await values.set(key, STRENGTH_PARAMETER, STRONGER)
        await values.set(key, FAILING_PARAMETER, NOT_FAILING)
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id, Stage.GEOMETRY)
        expect(
            [(change.before, change.after) for change in history]
            == [
                (None, {STRENGTH_PARAMETER: STRONGER}),
                ({STRENGTH_PARAMETER: STRONGER}, {STRENGTH_PARAMETER: STRONGER, FAILING_PARAMETER: NOT_FAILING}),
            ]
        )
        expect(all(change.layer is StepLayer.SETTINGS and change.source is ChangeSource.USER for change in history))
        expect(all(change.scope is ValueScope.PAGES and change.group_label == '' for change in history))
        expect(all(change.step_id == key.step_id for change in history))
        assert_expectations()

    async def test_the_value_the_page_already_has_changes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify setting a field to the value it has on the page writes no change and does not mark the stage stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        again = await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id)
        expect(record.state is StageState.FRESH)
        expect(again.changes == ())
        expect(len(history) == 1)
        assert_expectations()

    async def test_a_field_the_processor_does_not_have_is_refused_and_nothing_is_stored(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the processor checks the field, so an unknown name is a 422 and leaves the page as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        with pytest.raises(InvalidParametersError):
            await PageValues(fx_kit, actor, project.id).set(key, 'no_such_field', 1)
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(await fx_kit.uow().page_step_changes.list_for_page(page.id) == [])
        assert_expectations()

    async def test_unknown_step_other_stage_other_book_and_other_owner_are_not_found(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step no recipe of the stage has, a page of another book and a stranger's request are not found.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        _, other_project = await fx_kit.seed_project()
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        attempts = (
            (actor, project, evolve(key, step_id=StepId(uuid4()))),
            (actor, project, evolve(key, stage=Stage.CLEANUP)),
            (actor, other_project, key),
            (Actor(account_id=new_account_id()), project, key),
        )
        for who, book, address in attempts:
            with pytest.raises(NotFoundError):
                await PageValues(fx_kit, who, book.id).set(address, STRENGTH_PARAMETER, STRONGER)

    async def test_selected_pages_each_get_the_value_in_one_batch_and_one_undo_takes_it_back(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the pages named take the value as their own, the others do not, and one undo clears them all.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        target = on(ValueScope.PAGES, pages[0], pages[2])
        done = await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, target), STRONGER)
        expect(sorted(change.page_id for change in done.changes) == sorted([pages[0].id, pages[2].id]))
        expect(len({change.batch_id for change in done.changes}) == 1 and done.changes[0].batch_id == done.batch_id)
        expect(await strengths(fx_kit, actor, project, pages) == [STRONGER, 1, STRONGER, 1])
        expect(await stages_of(fx_kit, pages) == [StageState.STALE, StageState.FRESH] * 2)
        key = PageStepKey(pages[0].id, Stage.GEOMETRY, step_id)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(await strengths(fx_kit, actor, project, pages) == [1, 1, 1, 1])
        assert_expectations()

    async def test_a_page_of_another_book_is_not_found_and_nothing_is_written(self, fx_kit: ProcessingKit) -> None:
        """Verify a selected page the project does not have is refused before any page takes the value.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        _, other_project = await fx_kit.seed_project()
        stranger, _ = await fx_kit.seed_scan_page(other_project)
        target = on(ValueScope.PAGES, pages[0], stranger)
        with pytest.raises(NotFoundError):
            await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, target), STRONGER)
        assert await strengths(fx_kit, actor, project, pages) == [1, 1, 1, 1]


class TestTakingAPageValueBack:
    """Tests for taking the value of a page back."""

    async def test_field_is_taken_back_and_the_empty_state_is_deleted(self, fx_kit: ProcessingKit) -> None:
        """Verify taking back the last field deletes the state, writes the change and marks the stage stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        values = PageValues(fx_kit, actor, project.id)
        await values.set(key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        await values.take_back(key, STRENGTH_PARAMETER)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id)
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(record.state is StageState.STALE)
        expect((history[-1].before, history[-1].after) == ({STRENGTH_PARAMETER: STRONGER}, None))
        assert_expectations()

    async def test_the_manual_edit_stays_when_the_last_field_is_taken_back(self, fx_kit: ProcessingKit) -> None:
        """Verify the state keeps its manual edit when its settings are gone, and the other way round.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        values = PageValues(fx_kit, actor, project.id)
        edit = await fx_kit.edits().save(actor, project.id, key, ROTATION, None)
        await values.set(key, STRENGTH_PARAMETER, STRONGER)
        await values.take_back(key, STRENGTH_PARAMETER)
        left = await fx_kit.uow().page_step_states.get(key)
        await values.set(key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.edits().delete(actor, project.id, key)
        state = await fx_kit.uow().page_step_states.get(key)
        expect(left.params == {} and left.edit == edit)
        expect((state.params, state.edit) == ({STRENGTH_PARAMETER: STRONGER}, None))
        assert_expectations()

    async def test_a_field_the_page_does_not_change_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify taking back a field the page never changed is a NotFoundError.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        with pytest.raises(NotFoundError):
            await PageValues(fx_kit, actor, project.id).take_back(key, STRENGTH_PARAMETER)


class TestValuesForPartsOfTheBook:
    """Tests for the values of the odd pages, the even pages and a group, and the order the parts are laid in."""

    async def test_the_even_pages_take_the_value_and_only_they_go_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify the value is stored once, the even pages run with it, and the odd pages are left fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        done = await fx_kit.page_settings().change(
            actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER
        )
        stored = await fx_kit.uow().step_values.list_for_step(project.id, step_id)
        expect(
            [(values.scope, values.params) for values in stored] == [(ValueScope.EVEN, {STRENGTH_PARAMETER: STRONGER})]
        )
        expect(await strengths(fx_kit, actor, project, pages) == [1, STRONGER, 1, STRONGER])
        expect(await stages_of(fx_kit, pages) == [StageState.FRESH, StageState.STALE] * 2)
        expect(sorted(change.page_id for change in done.changes) == sorted([pages[1].id, pages[3].id]))
        expect(all(change.scope is ValueScope.EVEN and change.batch_id == done.batch_id for change in done.changes))
        expect(all((change.before, change.after) == (None, {STRENGTH_PARAMETER: STRONGER}) for change in done.changes))
        assert_expectations()

    async def test_the_odd_pages_take_a_value_the_even_pages_do_not(self, fx_kit: ProcessingKit) -> None:
        """Verify the odd and the even pages keep values of their own for one field.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, on(ValueScope.ODD)), STRONGER)
        await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGEST)
        assert await strengths(fx_kit, actor, project, pages) == [STRONGER, STRONGEST, STRONGER, STRONGEST]

    async def test_a_run_lays_a_page_over_a_group_over_a_side_over_the_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify the strongest part that has a value for a field gives it to the version of each page.

        The pages are at the places 1 to 4. The even pages have the value 2, the group of the second and third page has
        the value 3, and the third page has the value 4 of its own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        for page in pages[1:3]:
            await put_in_group(fx_kit, page, GROUP)
        settings = fx_kit.page_settings()
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER)
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGEST)
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.PAGES, pages[2])), OWN)
        await run_geometry(fx_kit, actor, project)
        ran = [(await head_of(fx_kit, page, Stage.GEOMETRY)).params[STRENGTH_PARAMETER] for page in pages]
        assert ran == [1, STRONGEST, OWN, STRONGER]

    async def test_taking_a_value_back_leaves_the_pages_the_next_part_by_strength(self, fx_kit: ProcessingKit) -> None:
        """Verify each page falls back to the next strongest value as the values above it are taken away.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        for page in pages[1:3]:
            await put_in_group(fx_kit, page, GROUP)
        settings = fx_kit.page_settings()
        group = strength_field(step_id, on(ValueScope.GROUP, label=GROUP))
        even = strength_field(step_id, on(ValueScope.EVEN))
        own = strength_field(step_id, on(ValueScope.PAGES, pages[1]))
        for field, value in ((even, STRONGER), (group, STRONGEST), (own, OWN)):
            await settings.change(actor, project.id, field, value)
        seen = [await strengths(fx_kit, actor, project, pages)]
        for field in (own, group, even):
            await settings.reset(actor, project.id, field)
            seen.append(await strengths(fx_kit, actor, project, pages))
        assert seen == [
            [1, OWN, STRONGEST, STRONGER],
            [1, STRONGEST, STRONGEST, STRONGER],
            [1, STRONGER, 1, STRONGER],
            [1, 1, 1, 1],
        ]
        assert await fx_kit.uow().step_values.list_for_step(project.id, step_id) == []

    async def test_a_change_goes_stale_only_the_pages_it_changes_the_parameters_of(self, fx_kit: ProcessingKit) -> None:
        """Verify an even page that takes the field from its group is untouched by a value for the even pages.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await put_in_group(fx_kit, pages[1], GROUP)
        settings = fx_kit.page_settings()
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGER)
        await run_geometry(fx_kit, actor, project)
        done = await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGEST)
        expect([change.page_id for change in done.changes] == [pages[3].id])
        expect(await stages_of(fx_kit, pages) == [StageState.FRESH] * 3 + [StageState.STALE])
        assert_expectations()

    async def test_a_group_value_goes_to_the_pages_of_the_group_and_to_a_page_that_joins_it_later(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page put in the group after the value was set runs with it, and a page that left it does not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await put_in_group(fx_kit, pages[0], GROUP)
        done = await fx_kit.page_settings().change(
            actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGER
        )
        expect(
            [(change.page_id, change.scope, change.group_label) for change in done.changes]
            == [(pages[0].id, ValueScope.GROUP, GROUP)]
        )
        expect(await strengths(fx_kit, actor, project, pages) == [STRONGER, 1, 1, 1])
        await put_in_group(fx_kit, pages[2], GROUP)
        await put_in_group(fx_kit, pages[0], '')
        expect(await strengths(fx_kit, actor, project, pages) == [1, 1, STRONGER, 1])
        assert_expectations()

    async def test_the_listing_gives_every_part_value_of_a_step_and_what_the_page_runs_with(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page lists the values of the parts it is not in too, so each can be taken back from any page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await put_in_group(fx_kit, pages[3], GROUP)
        settings = fx_kit.page_settings()
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER)
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGEST)
        [listed] = await settings.list(actor, project.id, pages[0].id, Stage.GEOMETRY)
        expect(listed.step_id == step_id)
        expect(listed.params == {})
        expect(
            [(part.scope, part.group_label) for part in listed.parts]
            == [(ValueScope.GROUP, GROUP), (ValueScope.EVEN, '')]
        )
        expect(listed.effective[STRENGTH_PARAMETER] == 1)
        assert_expectations()

    async def test_a_value_out_of_range_for_a_page_it_reaches_is_refused_and_nothing_is_stored(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a field the processor does not have is a 422 for a part of the pages too.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        field = ValueField(stage=Stage.GEOMETRY, step_id=step_id, name='no_such_field', target=on(ValueScope.EVEN))
        with pytest.raises(InvalidParametersError):
            await fx_kit.page_settings().change(actor, project.id, field, 1)
        expect(await fx_kit.uow().step_values.list_for_step(project.id, step_id) == [])
        expect(await stages_of(fx_kit, pages) == [StageState.FRESH] * 4)
        assert_expectations()

    async def test_taking_back_what_no_value_holds_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify taking a field back from a part of the pages that has no value for it is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, step_id = await ran_book(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.page_settings().reset(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)))

    async def test_a_group_with_no_page_in_it_has_nobody_to_take_the_value(self, fx_kit: ProcessingKit) -> None:
        """Verify a value for a group no page is in is refused instead of stored for nobody.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, step_id = await ran_book(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.page_settings().change(
                actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGER
            )

    async def test_a_page_that_changes_its_group_goes_stale_only_where_a_group_has_a_value(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify joining or leaving a group marks the stage stale when that group has a value, and not otherwise.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit, count=2)
        await put_in_group(fx_kit, pages[1], GROUP)
        await fx_kit.page_settings().change(
            actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGER
        )
        await run_geometry(fx_kit, actor, project)

        async def moved(page: Page, labels: set[str]) -> list[StageState]:
            """Mark the stage of a page for a change between groups, commit it and read the stages of the book.

            :param page: The page that changed its group.
            :type page: Page
            :param labels: The groups it left and joined.
            :type labels: set[str]
            :returns: The state of the stage of each page of the book.
            :rtype: list[StageState]
            """
            uow = fx_kit.uow()
            await fx_kit.parts(uow).records.mark_group_stale(project.id, page.id, labels)
            await uow.commit()
            return await stages_of(fx_kit, pages)

        expect(await moved(pages[0], {'', 'Plates'}) == [StageState.FRESH, StageState.FRESH])
        expect(await moved(pages[0], {'', GROUP}) == [StageState.STALE, StageState.FRESH])
        assert_expectations()

    async def test_books_built_from_one_profile_keep_their_values_apart(self, fx_kit: ProcessingKit) -> None:
        """Verify the values of a step in one book neither run in another book with the same step ids, nor get replaced.

        The second book holds copies of the recipes of the first with the same step identifiers, as two books built
        from one default profile do.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, first, _, step_id = await ran_book(fx_kit, count=2)
        await fx_kit.page_settings().change(actor, first.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER)
        other_actor, second = await fx_kit.seed_project()
        pages = [(await fx_kit.seed_scan_page(second, order_key=f'a{number}'))[0] for number in range(2)]
        for page in pages:
            await fx_kit.seed_base_version(page)
        uow = fx_kit.uow()
        for recipe in await uow.recipes.list_for_stage(first.id, Stage.GEOMETRY):
            await uow.recipes.add(evolve(recipe, id=RecipeId(uuid4()), project_id=second.id))
        await uow.commit()
        await run_geometry(fx_kit, other_actor, second)
        heads = [await head_of(fx_kit, page, Stage.GEOMETRY) for page in pages]
        expect([head.params[STRENGTH_PARAMETER] for head in heads] == [RECIPE_STRENGTH, RECIPE_STRENGTH])
        await fx_kit.page_settings().change(
            other_actor, second.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGEST
        )
        stored = [
            [values.params for values in await fx_kit.uow().step_values.list_for_step(project.id, step_id)]
            for project in (first, second)
        ]
        expect(stored == [[{STRENGTH_PARAMETER: STRONGER}], [{STRENGTH_PARAMETER: STRONGEST}]])
        assert_expectations()


class TestUndoOfAValueForParts:
    """Tests for taking back, from the history of a page, a value for the odd pages, the even pages or a group."""

    async def test_one_undo_takes_the_value_back_from_every_page_it_reached(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo asked on one even page writes the values back once and an undo in the history of each page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER)
        await run_geometry(fx_kit, actor, project)
        undone = await fx_kit.page_history().undo(
            actor, project.id, PageStepKey(pages[1].id, Stage.GEOMETRY, step_id), None
        )
        expect(sorted(undo.page_id for undo in undone) == sorted([pages[1].id, pages[3].id]))
        expect(all(undo.scope is ValueScope.EVEN and undo.source is ChangeSource.UNDO for undo in undone))
        expect(await fx_kit.uow().step_values.list_for_step(project.id, step_id) == [])
        expect(await strengths(fx_kit, actor, project, pages) == [1, 1, 1, 1])
        expect(await stages_of(fx_kit, pages) == [StageState.FRESH, StageState.STALE] * 2)
        assert_expectations()

    async def test_the_values_of_a_part_are_taken_back_from_the_newest_change_to_the_oldest(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify an undo back to the first change of two takes the value from the second to the first to none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        key = PageStepKey(pages[1].id, Stage.GEOMETRY, step_id)
        even = strength_field(step_id, on(ValueScope.EVEN))
        first = await fx_kit.page_settings().change(actor, project.id, even, STRONGER)
        await fx_kit.page_settings().change(actor, project.id, even, STRONGEST)
        newest = await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(len(newest) == 2)
        expect(
            [values.params for values in await fx_kit.uow().step_values.list_for_step(project.id, step_id)]
            == [{STRENGTH_PARAMETER: STRONGER}]
        )
        oldest = next(change for change in first.changes if change.page_id == pages[1].id)
        await fx_kit.page_history().undo(actor, project.id, key, oldest.id)
        expect(await fx_kit.uow().step_values.list_for_step(project.id, step_id) == [])
        assert_expectations()

    async def test_an_undo_marks_the_pages_the_part_covers_now_and_not_only_the_pages_of_its_history(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page that joined the group after the change goes stale when the change is taken back.

        The page that joined later has no change in its history, since the value was set before it was in the group, but
        it ran with the value and runs with the recipe once the value is gone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        await put_in_group(fx_kit, pages[0], GROUP)
        await fx_kit.page_settings().change(
            actor, project.id, strength_field(step_id, on(ValueScope.GROUP, label=GROUP)), STRONGER
        )
        await put_in_group(fx_kit, pages[2], GROUP)
        await run_geometry(fx_kit, actor, project)
        before = await stages_of(fx_kit, pages)
        await fx_kit.page_history().undo(actor, project.id, PageStepKey(pages[0].id, Stage.GEOMETRY, step_id), None)
        expect(before == [StageState.FRESH] * len(pages))
        expect(
            await stages_of(fx_kit, pages) == [StageState.STALE, StageState.FRESH, StageState.STALE, StageState.FRESH]
        )
        assert_expectations()

    async def test_an_undo_is_refused_when_the_values_changed_after_the_change(self, fx_kit: ProcessingKit) -> None:
        """Verify a change cannot be taken back over the values another change left, which would lose that change.

        The second change of the even pages does not reach the second page, which has a value of its own by then, so
        the history of that page does not hold it, and taking the first change back from there would lose it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit)
        key = PageStepKey(pages[1].id, Stage.GEOMETRY, step_id)
        even = strength_field(step_id, on(ValueScope.EVEN))
        settings = fx_kit.page_settings()
        await settings.change(actor, project.id, even, STRONGER)
        first = (await fx_kit.page_history().list(actor, project.id, key)).standing[0]
        await settings.change(actor, project.id, strength_field(step_id, on(ValueScope.PAGES, pages[1])), OWN)
        await settings.change(actor, project.id, even, STRONGEST)
        with pytest.raises(ConflictError):
            await fx_kit.page_history().undo(actor, project.id, key, first.id)
        assert [values.params for values in await fx_kit.uow().step_values.list_for_step(project.id, step_id)] == [
            {STRENGTH_PARAMETER: STRONGEST}
        ]


class TestRunWithPageSettings:
    """Tests for the run and the preview laying the settings of a page over the parameters of the recipe."""

    async def test_run_uses_the_page_value_and_leaves_the_other_pages_on_the_recipe(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the version of the page that changed a field runs with it, and the version of another page does not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        await fx_kit.seed_base_version(other)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        own, shared = await head_of(fx_kit, page, Stage.GEOMETRY), await head_of(fx_kit, other, Stage.GEOMETRY)
        expect(own.params[STRENGTH_PARAMETER] == STRONGER)
        expect(own.data[RAN_KEY] == STRONGER)
        expect(shared.params[STRENGTH_PARAMETER] == 1)
        assert_expectations()

    @patch(COUNT_BEFORE_PATCH, autospec=True, side_effect=InMemoryPageRepository.count_before)
    async def test_a_page_counts_its_place_once_however_many_steps_the_recipe_has(
        self, counting: MagicMock, fx_kit: ProcessingKit
    ) -> None:
        """Verify the place of a page is counted once for the chain of its steps, when a part of the pages has a value.

        :param counting: The count of the pages before a page, which still counts.
        :type counting: MagicMock
        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, step_id = await ran_book(fx_kit, count=2)
        step = await step_of(fx_kit, project)
        steps = [step, *(evolve(step, step_id=StepId(uuid4())) for _ in range(STEPS_OF_A_CHAIN - 1))]
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps, order=OrderMode.FREE))
        await fx_kit.page_settings().change(actor, project.id, strength_field(step_id, on(ValueScope.EVEN)), STRONGER)
        counting.reset_mock()
        await run_geometry(fx_kit, actor, project)
        assert counting.call_count == len(pages)

    async def test_the_page_keeps_its_value_when_the_recipe_changes_and_the_others_follow_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a field changed in the recipe reaches the pages that did not change it, and not the one that did.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        await fx_kit.seed_base_version(other)
        step = await step_of(fx_kit, project)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.parts(fx_kit.uow()).recipes.of_kind(project.id, Stage.GEOMETRY, RecipeKind.TEXT)
        draft = RecipeDraft(steps=[evolve(step, params={STRENGTH_PARAMETER: STRONGEST})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, draft)
        await run_geometry(fx_kit, actor, project)
        own, shared = await head_of(fx_kit, page, Stage.GEOMETRY), await head_of(fx_kit, other, Stage.GEOMETRY)
        expect((own.params[STRENGTH_PARAMETER], shared.params[STRENGTH_PARAMETER]) == (STRONGER, STRONGEST))
        assert_expectations()

    async def test_equal_effective_parameters_find_the_version_in_the_cache(self, fx_kit: ProcessingKit) -> None:
        """Verify the identifier hashes the parameters the step runs with, wherever each field came from.

        The page gets the value 2 from its own setting, then from the recipe, and the second run finds the version of
        the first in the cache instead of running the processor again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        step = await step_of(fx_kit, project)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        first, runs = await head_of(fx_kit, page, Stage.GEOMETRY), fx_kit.fake.runs
        await PageValues(fx_kit, actor, project.id).take_back(key, STRENGTH_PARAMETER)
        await fx_kit.parts(fx_kit.uow()).recipes.of_kind(project.id, Stage.GEOMETRY, RecipeKind.TEXT)
        draft = RecipeDraft(steps=[evolve(step, params={STRENGTH_PARAMETER: STRONGER})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, draft)
        await run_geometry(fx_kit, actor, project)
        second = await head_of(fx_kit, page, Stage.GEOMETRY)
        expect(second.id == first.id)
        expect(fx_kit.fake.runs == runs)
        assert_expectations()

    async def test_preview_lays_the_page_settings_over_the_steps_of_the_form(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview of a step of the form on the page runs with the field the page changed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        step = await step_of(fx_kit, project)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        form = evolve(step, params={STRENGTH_PARAMETER: STRONGEST})
        preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=(form,), step_index=0)
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.jobs().preview_step(job.id)
        previews = await fx_kit.uow().page_versions.list_for_stage(
            page.id, Stage.GEOMETRY, VersionScale.PREVIEW, EVERYTHING
        )
        [version] = previews.items
        assert version.params[STRENGTH_PARAMETER] == STRONGER


class TestRemakeWithPageSettings:
    """Tests for making again a version that was made with a setting of the page."""

    async def test_version_made_with_a_setting_is_made_again_after_the_setting_is_taken_back(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the stored parameters make the version again, though the page has no setting of the field now.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        first: PageVersion = await head_of(fx_kit, page, Stage.GEOMETRY)
        await PageValues(fx_kit, actor, project.id).take_back(key, STRENGTH_PARAMETER)
        await run_geometry(fx_kit, actor, project)
        assert (await fx_kit.uow().page_versions.get(first.id)).files_removed

        job = await fx_kit.service().start_remake(actor, project.id, page.id, first.id)
        await fx_kit.jobs().run_stage(job.id)

        remade = await fx_kit.uow().page_versions.get(first.id)
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.SUCCEEDED)
        expect((remade.files_removed, remade.params[STRENGTH_PARAMETER]) == (False, STRONGER))
        assert_expectations()
