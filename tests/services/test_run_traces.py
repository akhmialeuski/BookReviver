"""Tests for the spans a run of a stage makes: one for each page it runs, and one for each step it finds or makes."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import RecipeKind, RunOutcome, SpanAttribute, SpanName, Stage, StepOutcome, VersionScale
from bookreviver.domain.values import StageRun
from tests.helpers.processors import FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, BookRecord, Page
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
ORDER_KEYS: tuple[str, ...] = ('a0', 'a1')
UNKNOWN_PARAMETER: str = 'unknown'


async def seeded_pages(kit: ProcessingKit) -> tuple[Actor, BookRecord, list[Page]]:
    """Seed a book with two scan pages whose base versions are stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book and its pages in order.
    :rtype: tuple[Actor, BookRecord, list[Page]]
    """
    actor, book_record = await kit.seed_record()
    pages: list[Page] = []
    for order_key in ORDER_KEYS:
        page, _ = await kit.seed_scan_page(book_record, order_key=order_key)
        await kit.seed_base_version(book_record.id, page)
        pages.append(page)
    return actor, book_record, pages


class TestRunSpans:
    """Tests for the spans of a run of a stage over two pages, with the recording tracer of the kit."""

    async def test_run_makes_a_page_span_for_each_page_and_a_step_span_inside_each(self, fx_kit: ProcessingKit) -> None:
        """Verify each page is one ``run.page`` span with its stage and outcome, and its step is a ``step`` span in it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages = await seeded_pages(fx_kit)
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
        page_spans = fx_kit.tracer.named(SpanName.RUN_PAGE)
        step_spans = fx_kit.tracer.named(SpanName.STEP)
        expect(sorted(span.attributes[SpanAttribute.PAGE] for span in page_spans) == sorted(str(p.id) for p in pages))
        expect(all(span.attributes[SpanAttribute.STAGE] is Stage.GEOMETRY for span in page_spans))
        expect(all(span.attributes[SpanAttribute.RUN_OUTCOME] is RunOutcome.DONE for span in page_spans))
        expect(len(step_spans) == len(pages))
        expect(all(span.attributes[SpanAttribute.STEP_TYPE] == FAKE_KEY for span in step_spans))
        expect(all(span.attributes[SpanAttribute.STEP_SCALE] is VersionScale.FULL for span in step_spans))
        expect(all(span.parent in page_spans for span in step_spans))
        assert_expectations()

    async def test_every_step_of_the_first_run_ran_and_none_of_the_second_did(self, fx_kit: ProcessingKit) -> None:
        """Verify the outcome of a step is ``ran`` when the processor made the version, and ``cached`` when found.

        The second run names the pages, which a run of the whole book would leave out as fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages = await seeded_pages(fx_kit)
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
        first = [span.attributes[SpanAttribute.STEP_OUTCOME] for span in fx_kit.tracer.named(SpanName.STEP)]
        fx_kit.tracer.finished.clear()
        named = StageRun(stage=Stage.GEOMETRY, page_ids=tuple(page.id for page in pages))
        await run_stage(fx_kit, actor, book_record, named)
        second = [span.attributes[SpanAttribute.STEP_OUTCOME] for span in fx_kit.tracer.named(SpanName.STEP)]
        expect(first == [StepOutcome.RAN] * len(ORDER_KEYS))
        expect(second == [StepOutcome.CACHED] * len(ORDER_KEYS))
        expect(fx_kit.fake.runs == len(ORDER_KEYS))
        assert_expectations()

    async def test_a_step_the_processor_refuses_the_parameters_of_ends_failed(self, fx_kit: ProcessingKit) -> None:
        """Verify a step that leaves through an exception still has the outcome ``failed``, not none.

        The recipe is stored with a parameter the fake processor does not know, which the service of the recipes would
        have refused, so ``validate_params`` raises inside the ``step`` span before the version is looked for.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _ = await seeded_pages(fx_kit)
        recipe = await fx_kit.recipe_of(actor, book_record, Stage.GEOMETRY, RecipeKind.TEXT)
        refused = evolve(recipe.steps[0], params=fx_kit.params(**{UNKNOWN_PARAMETER: 1}))
        await fx_kit.databases.open(book_record.id).change(
            lambda repos, _book_record: repos.recipes.update(evolve(recipe, steps=(refused,)))
        )
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
        outcomes = [span.attributes.get(SpanAttribute.STEP_OUTCOME) for span in fx_kit.tracer.named(SpanName.STEP)]
        expect(outcomes == [StepOutcome.FAILED] * len(ORDER_KEYS))
        expect(fx_kit.fake.runs == 0)
        assert_expectations()
