"""Tests for the identity of a step and its condition: two steps of one processor in a recipe, and a page that skips one.

The fake processor of the geometry stage records its strength in the data of its output, so a test reads from the
versions of a page which steps ran on it and which passed it unchanged.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import AppliesTo, BlankFill, EditorKind, PageKind, Stage, TransformKind, VersionData
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.values import NewPageEdit, PageStepKey, RecipeDraft, StageRun, Step
from tests.helpers.processors import RAN_KEY, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import head_of, run_stage

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
# Names of the recipes the tests save
TWO_STEPS: str = 'Two'
PICTURES_ONLY: str = 'Pictures'
ALL_PAGES: str = 'Everything'


async def input_of(kit: ProcessingKit, version: PageVersion) -> PageVersion:
    """Read the version a version was made from.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param version: A version that has an input.
    :type version: PageVersion
    :returns: The input version.
    :rtype: PageVersion
    """
    assert version.input_id is not None
    return await kit.stored_version(version.input_id)


async def text_and_plate(kit: ProcessingKit) -> tuple[Actor, Project, Page, Page]:
    """Seed a project with a text page and a plate, each with its base version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the text page and the plate.
    :rtype: tuple[Actor, Project, Page, Page]
    """
    actor, project = await kit.seed_project()
    text, _ = await kit.seed_scan_page(project, order_key='a0')
    plate, _ = await kit.seed_scan_page(project, order_key='a1', kind=PageKind.PLATE)
    for page in (text, plate):
        await kit.seed_base_version(page)
    return actor, project, text, plate


class TestCondition:
    """Tests for the pages a step processes."""

    async def test_two_steps_of_one_processor_each_process_only_the_pages_of_their_condition(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the text page passes the step for pictures and the plate passes the step for text.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, plate = await text_and_plate(fx_kit)
        steps = [
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 1}, applies_to=AppliesTo.TEXT),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2}, applies_to=AppliesTo.PICTURES),
        ]
        two = RecipeDraft(name=TWO_STEPS, steps=steps)
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, two)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        text_last = await head_of(fx_kit, text, Stage.GEOMETRY)
        text_first = await input_of(fx_kit, text_last)
        plate_last = await head_of(fx_kit, plate, Stage.GEOMETRY)
        plate_first = await input_of(fx_kit, plate_last)
        expect(fx_kit.fake.runs == 2)
        expect(text_first.data[RAN_KEY] == 1 and VersionData.SKIPPED_BY_CONDITION not in text_first.data)
        expect(text_last.data[VersionData.SKIPPED_BY_CONDITION] is True)
        expect(plate_first.data[VersionData.SKIPPED_BY_CONDITION] is True)
        expect(plate_last.data[RAN_KEY] == 2 and VersionData.SKIPPED_BY_CONDITION not in plate_last.data)
        assert_expectations()

    async def test_a_skipped_step_makes_an_identity_version_without_parameters_or_review_mark(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the version of a skipped step is the identity, holds the image of its input, and marks nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await text_and_plate(fx_kit)
        step = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 5}, applies_to=AppliesTo.PICTURES)
        pictures = RecipeDraft(name=PICTURES_ONLY, steps=[step])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, pictures)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        skipped = await head_of(fx_kit, text, Stage.GEOMETRY)
        # Only the plate of the book met the condition
        expect(fx_kit.fake.runs == 1)
        expect(skipped.transform.kind is TransformKind.IDENTITY)
        expect((skipped.params, skipped.review, skipped.edit_hash) == ({}, None, ''))
        expect(skipped.data[VersionData.SKIPPED] is True and skipped.data[VersionData.SKIPPED_BY_CONDITION] is True)
        expect(skipped.renditions is not None and skipped.renditions.ready)
        assert_expectations()

    async def test_the_parameters_of_a_step_that_skips_a_page_do_not_make_the_page_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page that skips a step finds its version again when only the parameters of that step change.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await text_and_plate(fx_kit)
        step = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 5}, applies_to=AppliesTo.PICTURES)
        pictures = RecipeDraft(name=PICTURES_ONLY, steps=[step])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, pictures)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        before = await head_of(fx_kit, text, Stage.GEOMETRY)
        changed = Step(
            processor_key=FAKE_KEY,
            params={STRENGTH_PARAMETER: 6},
            applies_to=AppliesTo.PICTURES,
            step_id=step.step_id,
        )
        changed_draft = RecipeDraft(name=PICTURES_ONLY, steps=[changed])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, changed_draft)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        assert (await head_of(fx_kit, text, Stage.GEOMETRY)).id == before.id

    async def test_a_page_whose_kind_changes_is_made_again_by_the_step_it_now_meets(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the skip is part of the identifier, so the step that ran is not mistaken for the one that passed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await text_and_plate(fx_kit)
        step = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 3}, applies_to=AppliesTo.PICTURES)
        pictures = RecipeDraft(name=PICTURES_ONLY, steps=[step])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, pictures)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        skipped = await head_of(fx_kit, text, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.pages.update(evolve(await uow.pages.get(text.id), kind=PageKind.PLATE))
        await uow.commit()
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        made = await head_of(fx_kit, text, Stage.GEOMETRY)
        expect(made.id != skipped.id)
        expect(made.data[RAN_KEY] == 3 and VersionData.SKIPPED_BY_CONDITION not in made.data)
        assert_expectations()


class TestLeaf:
    """Tests for a page that shows a leaf the program drew, which every step of every stage passes unchanged."""

    async def test_a_page_that_shows_a_leaf_passes_every_step_unchanged_and_unmarked(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify no step runs on the leaf, and its version is the identity with no parameters and no review mark.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project, kind=PageKind.BLANK)
        await fx_kit.seed_base_version(page)
        uow = fx_kit.uow()
        await uow.pages.update(evolve(await uow.pages.get(page.id), blank_fill=BlankFill.WHITE))
        await uow.commit()
        steps = [Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: strength}) for strength in (4, 5)]
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, ALL_PAGES, steps)

        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))

        passed = await head_of(fx_kit, page, Stage.GEOMETRY)
        expect(fx_kit.fake.runs == 0)
        expect(passed.transform.kind is TransformKind.IDENTITY)
        expect((passed.params, passed.review, passed.edit_hash) == ({}, None, ''))
        expect(passed.data[VersionData.SKIPPED] is True and passed.data[VersionData.SKIPPED_BY_CONDITION] is True)
        expect(passed.renditions is not None and passed.renditions.ready)
        assert_expectations()

    async def test_the_page_is_processed_by_the_steps_again_when_it_shows_its_scan_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the leaf and the scan do not share a version, so the scan is run by the step and not found as passed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project, kind=PageKind.BLANK)
        await fx_kit.seed_base_version(page)
        uow = fx_kit.uow()
        await uow.pages.update(evolve(await uow.pages.get(page.id), blank_fill=BlankFill.WHITE))
        await uow.commit()
        step = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 4})
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, ALL_PAGES, [step])
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        passed = await head_of(fx_kit, page, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.pages.update(evolve(await uow.pages.get(page.id), blank_fill=BlankFill.SCAN))
        await uow.commit()

        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))

        made = await head_of(fx_kit, page, Stage.GEOMETRY)
        expect(fx_kit.fake.runs == 1)
        expect(made.id != passed.id)
        expect(made.data[RAN_KEY] == 4 and VersionData.SKIPPED_BY_CONDITION not in made.data)
        assert_expectations()


class TestIdentity:
    """Tests for the identifier of a step, which its manual edits and its copies keep."""

    async def test_an_edit_of_one_of_two_steps_of_one_processor_does_not_reach_the_other(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the version of the step with the edit has its hash, and the version of the other step has none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await text_and_plate(fx_kit)
        first = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 1})
        second = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})
        two = RecipeDraft(name=TWO_STEPS, steps=[first, second])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, two)
        await fx_kit.edits().save(
            actor, project.id, PageStepKey(text.id, Stage.GEOMETRY, second.step_id), ROTATION, None
        )
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        last = await head_of(fx_kit, text, Stage.GEOMETRY)
        earlier = await input_of(fx_kit, last)
        expect(last.edit_hash != '')
        expect(earlier.edit_hash == '')
        assert_expectations()

    async def test_the_identifier_of_a_step_survives_a_reorder_a_save_and_a_variant(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the identifiers stay with their steps when the recipe is saved in another order, and in a variant.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await text_and_plate(fx_kit)
        first = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 1})
        second = Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})
        saved = await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=TWO_STEPS, steps=[first, second])
        )
        reordered = await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=TWO_STEPS, steps=[second, first])
        )
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Copy', steps=reordered.steps)
        )
        expect([step.step_id for step in saved.steps] == [first.step_id, second.step_id])
        expect([step.step_id for step in reordered.steps] == [second.step_id, first.step_id])
        expect([step.step_id for step in variant.steps] == [second.step_id, first.step_id])
        assert_expectations()

    async def test_the_identifier_and_the_condition_survive_a_profile(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile keeps the identifier and the condition of each step, and applying it gives them back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await text_and_plate(fx_kit)
        steps = [
            Step(processor_key=FAKE_KEY, applies_to=AppliesTo.TEXT),
            Step(processor_key=FAKE_KEY, applies_to=AppliesTo.COLOR_PICTURES),
        ]
        profile = await fx_kit.profiles().save(actor, Stage.GEOMETRY, RecipeDraft(name=TWO_STEPS, steps=steps))
        applied = await fx_kit.profiles().apply(actor, project.id, profile.id, activate=True)
        wanted = [(step.step_id, step.applies_to) for step in steps]
        expect([(step.step_id, step.applies_to) for step in profile.steps] == wanted)
        expect([(step.step_id, step.applies_to) for step in applied.recipe.steps] == wanted)
        assert_expectations()

    async def test_a_step_added_to_a_recipe_gets_a_new_identifier(self, fx_kit: ProcessingKit) -> None:
        """Verify a step the interface adds, which names no identifier, gets its own and keeps the others.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await text_and_plate(fx_kit)
        kept = Step(processor_key=FAKE_KEY)
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, RecipeDraft(name='One', steps=[kept]))
        added = Step(processor_key=FAKE_KEY)
        recipe = await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=TWO_STEPS, steps=[kept, added])
        )
        ids = [step.step_id for step in recipe.steps]
        assert (ids[0] == kept.step_id, len(set(ids))) == (True, 2)
