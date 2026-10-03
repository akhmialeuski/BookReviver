"""Tests for the settings a page has for a step of a recipe, and for the run that lays them over the recipe."""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import ChangeSource, EditorKind, JobState, Stage, StageState, StepLayer, VersionScale
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import StepId
from bookreviver.domain.values import NewPageEdit, PageStageKey, SliceRequest, StepPreview
from tests.helpers.builders import EPOCH, new_account_id
from tests.helpers.processors import FAILING_PARAMETER, RAN_KEY, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import head_of
from tests.services.test_processing_remake import AFTER_RETENTION_DAYS, run_geometry
from tests.services.test_processing_versions import ran_geometry

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion, Project
    from bookreviver.domain.values import Step
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRONGER: int = 2
STRONGEST: int = 3
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
    return (await kit.parts(kit.uow()).recipes.active(project.id, Stage.GEOMETRY)).steps[0]


class TestChange:
    """Tests for PageSettingsService.change."""

    async def test_field_is_stored_for_the_page_alone_and_marks_the_stage_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a changed field is listed for its page only, the stage goes stale and nothing is computed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        runs = fx_kit.fake.runs
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        stored = await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        listed = await fx_kit.page_settings().list(actor, project.id, page.id, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(stored.params == {STRENGTH_PARAMETER: STRONGER})
        expect(listed == [stored])
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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.page_settings().change(actor, project.id, key, FAILING_PARAMETER, NOT_FAILING)
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id, Stage.GEOMETRY)
        expect(
            [(change.before, change.after) for change in history]
            == [
                (None, {STRENGTH_PARAMETER: STRONGER}),
                ({STRENGTH_PARAMETER: STRONGER}, {STRENGTH_PARAMETER: STRONGER, FAILING_PARAMETER: NOT_FAILING}),
            ]
        )
        expect(all(change.layer is StepLayer.SETTINGS and change.source is ChangeSource.USER for change in history))
        expect(all(change.step_id == key.step_id and change.batch_id is None for change in history))
        assert_expectations()

    async def test_the_value_the_page_already_has_changes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify setting a field to the value it has on the page writes no change and does not mark the stage stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id)
        expect(record.state is StageState.FRESH)
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
            await fx_kit.page_settings().change(actor, project.id, key, 'no_such_field', 1)
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
                await fx_kit.page_settings().change(who, book.id, address, STRENGTH_PARAMETER, STRONGER)


class TestReset:
    """Tests for PageSettingsService.reset."""

    async def test_field_is_taken_back_and_the_empty_state_is_deleted(self, fx_kit: ProcessingKit) -> None:
        """Verify taking back the last field deletes the state, writes the change and marks the stage stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        left = await fx_kit.page_settings().reset(actor, project.id, key, STRENGTH_PARAMETER)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        history = await fx_kit.uow().page_step_changes.list_for_page(page.id)
        expect(left.is_empty)
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
        edit = await fx_kit.edits().save(actor, project.id, key, ROTATION, None)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        left = await fx_kit.page_settings().reset(actor, project.id, key, STRENGTH_PARAMETER)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
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
            await fx_kit.page_settings().reset(actor, project.id, key, STRENGTH_PARAMETER)


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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        own, shared = await head_of(fx_kit, page, Stage.GEOMETRY), await head_of(fx_kit, other, Stage.GEOMETRY)
        expect(own.params[STRENGTH_PARAMETER] == STRONGER)
        expect(own.data[RAN_KEY] == STRONGER)
        expect(shared.params[STRENGTH_PARAMETER] == 1)
        assert_expectations()

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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        recipe = await fx_kit.parts(fx_kit.uow()).recipes.active(project.id, Stage.GEOMETRY)
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, recipe.name, [evolve(step, params={STRENGTH_PARAMETER: STRONGEST})]
        )
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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        first, runs = await head_of(fx_kit, page, Stage.GEOMETRY), fx_kit.fake.runs
        await fx_kit.page_settings().reset(actor, project.id, key, STRENGTH_PARAMETER)
        recipe = await fx_kit.parts(fx_kit.uow()).recipes.active(project.id, Stage.GEOMETRY)
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, recipe.name, [evolve(step, params={STRENGTH_PARAMETER: STRONGER})]
        )
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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
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
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await run_geometry(fx_kit, actor, project)
        first: PageVersion = await head_of(fx_kit, page, Stage.GEOMETRY)
        await fx_kit.page_settings().reset(actor, project.id, key, STRENGTH_PARAMETER)
        await run_geometry(fx_kit, actor, project)
        fx_kit.clock.moment = EPOCH + timedelta(days=AFTER_RETENTION_DAYS)
        collection = await fx_kit.service().start_collection(actor, project.id)
        await fx_kit.jobs().collect_versions(collection.id)
        assert (await fx_kit.uow().page_versions.get(first.id)).files_removed

        job = await fx_kit.service().start_remake(actor, project.id, page.id, first.id)
        await fx_kit.jobs().run_stage(job.id)

        remade = await fx_kit.uow().page_versions.get(first.id)
        expect((await fx_kit.uow().jobs.get(job.id)).state is JobState.SUCCEEDED)
        expect((remade.files_removed, remade.params[STRENGTH_PARAMETER]) == (False, STRONGER))
        assert_expectations()
