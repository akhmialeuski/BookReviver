"""Tests for the side of the book a step reads: the processor gets it, and a page that changes sides is made again."""

from typing import TYPE_CHECKING, override

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import PageSide, Stage
from bookreviver.domain.values import PageStageKey, StageRun
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import StepResult
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from tests.helpers.builders import SPLIT_NONE
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Page, PageVersion
    from bookreviver.ports.processing import StepInput

pytestmark = pytest.mark.anyio

SIDE_KEY: str = 'side'
SIDED_KEY: str = 'geometry.sided'


class SidedProcessor(FakeProcessor):
    """A geometry processor that depends on the side of the page and records the side it was given."""

    spec = evolve(FakeProcessor.spec, key=SIDED_KEY, by_page_side=True)

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Copy the input and record the side of the page.

        :param step_input: What the step reads.
        :type step_input: StepInput
        :returns: One output holding the input image, whose data names the side.
        :rtype: StepResult
        """
        [output] = super().run(step_input).outputs
        side = None if step_input.side is None else step_input.side.value
        return StepResult(outputs=[evolve(output, data={SIDE_KEY: side})])


@pytest.fixture
def fx_sided_kit(fx_asset_store: LocalAssetStore) -> ProcessingKit:
    """Build a processing kit whose Geometry recipe is the one step that reads the side of the page.

    :param fx_asset_store: Local asset store over the test's storage root.
    :type fx_asset_store: LocalAssetStore
    :returns: The kit.
    :rtype: ProcessingKit
    """
    defaults = DefaultRecipes(
        {
            Stage.PAGE_SPLIT: (RecipeTemplate(name='Whole scan', processor_keys=(SPLIT_NONE.key,)),),
            Stage.GEOMETRY: (RecipeTemplate(name='Sided', processor_keys=(SIDED_KEY,)),),
        }
    )
    return ProcessingKit(fx_asset_store, processors=[SplitNone(), SidedProcessor()], defaults=defaults)


async def head_of(kit: ProcessingKit, page: Page) -> PageVersion:
    """Read the current version of the Geometry stage of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :returns: The version the stage record names.
    :rtype: PageVersion
    """
    reader = kit.uow()
    record = await reader.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
    assert record.head_version_id is not None
    return await reader.page_versions.get(record.head_version_id)


class TestPageSide:
    """Tests for the side of the page a step reads."""

    async def test_an_odd_place_is_a_right_page_and_a_page_that_changes_sides_is_made_again(
        self, fx_sided_kit: ProcessingKit
    ) -> None:
        """Verify a page that changes sides has its step run again with the other side.

        The first page of the book is a right page, a page put before it moves it to the left, and a version of the same
        inputs would not be made again.

        :param fx_sided_kit: The processing kit with the step that reads the side.
        :type fx_sided_kit: ProcessingKit
        """
        actor, project = await fx_sided_kit.seed_project()
        pages = []
        for order_key in ('b0', 'b1'):
            page, _ = await fx_sided_kit.seed_scan_page(project, order_key=order_key)
            await fx_sided_kit.seed_base_version(page)
            pages.append(page)
        run = StageRun(stage=Stage.GEOMETRY)
        job = await fx_sided_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        await fx_sided_kit.jobs().run_stage(job.id)
        # The run queues a collection of old versions, which has to end before the next run is accepted
        await fx_sided_kit.work_queue()
        before = [await head_of(fx_sided_kit, page) for page in pages]
        newcomer, _ = await fx_sided_kit.seed_scan_page(project, order_key='a0')
        await fx_sided_kit.seed_base_version(newcomer)
        job = await fx_sided_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        await fx_sided_kit.jobs().run_stage(job.id)
        after = [await head_of(fx_sided_kit, page) for page in pages]
        expect([version.data[SIDE_KEY] for version in before] == [PageSide.RIGHT.value, PageSide.LEFT.value])
        expect([version.data[SIDE_KEY] for version in after] == [PageSide.LEFT.value, PageSide.RIGHT.value])
        expect(all(old.id != new.id for old, new in zip(before, after, strict=True)))
        assert_expectations()
