"""Tests for the budget of a preview of a step on the visible page, which they read from the ``step`` span.

The preview goes through the processing services as a request and a worker would take it, with the real
``geometry.deskew``, so the span covers the values laid over the recipe, the processor, the version written and the
files stored, which is what the report of a run measures as well. The tests need OpenCV, and are skipped with the
reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import SpanAttribute, SpanName, Stage, StepOutcome, VersionData, VersionScale
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.values import Step, StepPreview
from scripts import trace_report
from tests.helpers.samples import png_bytes, text_page, turned

if TYPE_CHECKING:
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

DESKEW_KEY: str = 'geometry.deskew'
NANOSECONDS_PER_MILLISECOND: int = 1_000_000
# The page of the preview, which is as long as a preview goes, and the angle it was turned by, in degrees
PAGE_WIDTH_PX: int = 1_400
PAGE_HEIGHT_PX: int = 2_048
SKEW_DEGREES: float = 2.0
# How far the angle that is found may be from the opposite of the skew, in degrees
ANGLE_TOLERANCE: float = 0.2


class TestPreviewBudget:
    """Tests for the time of a preview of the visible page, read from the span of its step."""

    async def test_preview_of_the_visible_page_fits_the_budget(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the ``step`` span of the deskew at the preview scale ran the processor and ended within the budget.

        :param fx_cv_kit: The processing kit with the OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        page, _ = await fx_cv_kit.seed_scan_page(project)
        scan = turned(text_page(PAGE_WIDTH_PX, PAGE_HEIGHT_PX), SKEW_DEGREES)
        await fx_cv_kit.seed_base_version(page, image=png_bytes(scan))
        preview = StepPreview(
            page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=DESKEW_KEY),), step_index=0
        )
        job = await fx_cv_kit.service().start_preview(actor, project.id, preview)
        await fx_cv_kit.jobs().preview_step(job.id)
        [span] = [
            span
            for span in fx_cv_kit.tracer.named(SpanName.STEP)
            if span.attributes[SpanAttribute.STEP_TYPE] == DESKEW_KEY
            and span.attributes[SpanAttribute.STEP_SCALE] is VersionScale.PREVIEW
        ]
        [ready] = [event for event in fx_cv_kit.events.published if isinstance(event, PageVersionReady)]
        assert span.duration_ns is not None
        expect(span.attributes[SpanAttribute.STEP_OUTCOME] is StepOutcome.RAN)
        # The limit is the one the report of a run checks, so the test and the report cannot drift apart
        duration_ms = span.duration_ns / NANOSECONDS_PER_MILLISECOND
        expect(duration_ms < trace_report.PREVIEW_BUDGET.limit_ms, f'The step took {duration_ms} ms.')
        # A page the step did not find the lines of would end as fast, and the budget would say nothing
        expect(abs(ready.version.data[VersionData.ANGLE] + SKEW_DEGREES) < ANGLE_TOLERANCE)
        assert_expectations()
