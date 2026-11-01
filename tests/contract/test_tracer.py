"""Contract of the Tracer port, run against the adapters of ``TracerAdapter``.

What a caller sees holds for the three adapters: a block runs its body once and lets an exception go on unchanged, and
a started span can be given an attribute and ended. What a reader of the spans sees holds for the two adapters that
keep them, since the null tracer by its nature keeps none.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.jobs import JobKind
from bookreviver.domain.spans import SpanAttribute, SpanName, SpanParent
from tests.harness.adapters.tracers import FinishedSpan, open_tracer_under_test

if TYPE_CHECKING:
    from pathlib import Path

    from tests.harness.adapters.tracers import TracerAdapter

LOCK_WAIT_MS: float = 1.5
HTTP_METHOD: str = 'GET'
STATUS_OK: int = HTTPStatus.OK
EXPLODED: str = 'The step exploded.'


class TestCaller:
    """What a caller of ``span`` and ``start`` sees, on every adapter."""

    def test_nested_blocks_run_their_bodies_once_in_order(
        self, fx_tracer_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify each body runs once, the inner inside the outer, and a block yields a span that takes an attribute.

        :param fx_tracer_adapter: Adapter under test.
        :type fx_tracer_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        # The name of the span a body runs in, noted each time the body takes a step
        order: list[SpanName] = []
        with (
            open_tracer_under_test(fx_tracer_adapter, tmp_path) as under_test,
            under_test.tracer.span(SpanName.JOB, {SpanAttribute.JOB_KIND: JobKind.RUN_STAGE}) as outer,
        ):
            order.append(SpanName.JOB)
            with under_test.tracer.span(SpanName.RUN_PAGE) as inner:
                order.append(SpanName.RUN_PAGE)
                inner.set_attribute(SpanAttribute.PAGE, 'page')
            outer.set_attribute(SpanAttribute.STATUS, STATUS_OK)
            order.append(SpanName.JOB)
        assert order == [SpanName.JOB, SpanName.RUN_PAGE, SpanName.JOB]

    def test_exception_leaves_the_block_unchanged(self, fx_tracer_adapter: TracerAdapter, tmp_path: Path) -> None:
        """Verify the exception of a body is the one the caller catches, not a copy or a wrapper.

        :param fx_tracer_adapter: Adapter under test.
        :type fx_tracer_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        error = ValueError(EXPLODED)
        with (
            open_tracer_under_test(fx_tracer_adapter, tmp_path) as under_test,
            pytest.raises(ValueError, match=EXPLODED) as caught,
            under_test.tracer.span(SpanName.STEP),
        ):
            raise error
        assert caught.value is error

    def test_started_span_takes_an_attribute_and_ends_without_a_block(
        self, fx_tracer_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify a span that is started, given an attribute and ended needs no block, and leaves no block open.

        :param fx_tracer_adapter: Adapter under test.
        :type fx_tracer_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with open_tracer_under_test(fx_tracer_adapter, tmp_path) as under_test:
            started = under_test.tracer.start(SpanName.WRITE_BLOCK, {SpanAttribute.WAIT_MS: LOCK_WAIT_MS})
            started.set_attribute(SpanAttribute.HOLD_MS, LOCK_WAIT_MS)
            started.end()
            # A block opened after the started span ended is a root, which the readers of the next tests check
            with under_test.tracer.span(SpanName.JOB):
                pass


class TestSpans:
    """What a reader of the spans of an adapter that keeps them sees."""

    def test_span_opened_inside_a_block_is_its_child_and_ends_first(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify nesting: the inner span names the outer as its parent, and the outer is a root that ends last.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with (
            open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test,
            under_test.tracer.span(SpanName.JOB),
            under_test.tracer.span(SpanName.RUN_PAGE),
            under_test.tracer.span(SpanName.STEP),
        ):
            pass
        finished = under_test.finished()
        assert [(span.name, span.parent) for span in finished] == [
            (SpanName.STEP, SpanName.RUN_PAGE),
            (SpanName.RUN_PAGE, SpanName.JOB),
            (SpanName.JOB, None),
        ]

    def test_started_span_is_not_current_but_is_a_child_of_the_current_one(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify a started span has the current span as its parent, and a block opened meanwhile does not nest in it.

        The started span ends explicitly, after the block that was current when it started has ended too.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test:
            with under_test.tracer.span(SpanName.JOB):
                started = under_test.tracer.start(SpanName.WRITE_BLOCK)
                with under_test.tracer.span(SpanName.RUN_PAGE):
                    pass
            started.end()
        parents = {span.name: span.parent for span in under_test.finished()}
        expect(parents[SpanName.WRITE_BLOCK.value] == SpanName.JOB.value)
        expect(parents[SpanName.RUN_PAGE.value] == SpanName.JOB.value)
        expect(parents[SpanName.JOB.value] is None)
        assert_expectations()

    def test_span_asked_to_have_no_parent_is_a_root_inside_another_span(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify a block and a started span with no parent are roots, and a block with none is still current.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with (
            open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test,
            under_test.tracer.span(SpanName.JOB),
        ):
            # The span of the block is current for what is opened inside it, whatever its own parent is
            with (
                under_test.tracer.span(SpanName.RUN_PAGE, parent=SpanParent.NONE),
                under_test.tracer.span(SpanName.STEP),
            ):
                pass
            under_test.tracer.start(SpanName.WRITE_BLOCK, parent=SpanParent.NONE).end()
        parents = {span.name: span.parent for span in under_test.finished()}
        expect(parents[SpanName.RUN_PAGE.value] is None)
        expect(parents[SpanName.WRITE_BLOCK.value] is None)
        expect(parents[SpanName.STEP.value] == SpanName.RUN_PAGE.value)
        expect(parents[SpanName.JOB.value] is None)
        assert_expectations()

    def test_attributes_given_at_the_start_and_after_it_are_kept(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify an attribute set after the start is on the span with those of the start, for a block and a started span.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test:
            with under_test.tracer.span(SpanName.HTTP_REQUEST, {SpanAttribute.METHOD: HTTP_METHOD}) as block:
                block.set_attribute(SpanAttribute.STATUS, STATUS_OK)
            started = under_test.tracer.start(SpanName.WRITE_BLOCK, {SpanAttribute.WAIT_MS: LOCK_WAIT_MS})
            started.set_attribute(SpanAttribute.HOLD_MS, LOCK_WAIT_MS)
            started.end()
        attributes = {span.name: span.attributes for span in under_test.finished()}
        expect(
            attributes[SpanName.HTTP_REQUEST.value]
            == {SpanAttribute.METHOD: HTTP_METHOD, SpanAttribute.STATUS: STATUS_OK}
        )
        expect(
            attributes[SpanName.WRITE_BLOCK.value]
            == {SpanAttribute.WAIT_MS: LOCK_WAIT_MS, SpanAttribute.HOLD_MS: LOCK_WAIT_MS}
        )
        assert_expectations()

    def test_exception_is_recorded_on_the_span_it_left_and_not_on_one_that_caught_it(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify the span of the block an exception left is marked failed, and the span that caught it is not.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with (
            open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test,
            under_test.tracer.span(SpanName.RUN_PAGE),
            pytest.raises(ValueError, match=EXPLODED),
            under_test.tracer.span(SpanName.STEP),
        ):
            raise ValueError(EXPLODED)
        failed = {span.name: span.failed for span in under_test.finished()}
        assert failed == {SpanName.STEP.value: True, SpanName.RUN_PAGE.value: False}

    def test_started_span_that_was_never_ended_is_not_a_finished_span(
        self, fx_recording_adapter: TracerAdapter, tmp_path: Path
    ) -> None:
        """Verify a span only counts once it is ended, so a block whose transaction never ended is not reported.

        :param fx_recording_adapter: Adapter under test, one that keeps its spans.
        :type fx_recording_adapter: TracerAdapter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with open_tracer_under_test(fx_recording_adapter, tmp_path) as under_test:
            under_test.tracer.start(SpanName.WRITE_BLOCK)
            with under_test.tracer.span(SpanName.JOB):
                pass
        assert under_test.finished() == [
            FinishedSpan(name=SpanName.JOB.value, attributes={}, parent=None, failed=False)
        ]
