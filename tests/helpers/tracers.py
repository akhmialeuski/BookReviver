"""Every Tracer adapter set up with a reader of the spans it recorded, for the port contract and the adapter tests.

The null tracer keeps nothing, the recording tracer keeps its spans in memory, and the OpenTelemetry tracer writes them
into a file when it is closed, so the reader of each is a function that turns what the adapter holds into one shape,
``FinishedSpan``, which the contract compares across adapters. The file of the OpenTelemetry adapter is complete only
once its block is left, so a test reads the spans after the ``with`` block of ``open_tracer_under_test``. The reader of
the file is the parser of the trace report, ``load_spans``, so the contract suite is also what proves that the report
reads what the real exporter writes.

``AskingTracer`` is the recording tracer for a test that has to wait until a span has started, such as a write block
asking for the lock or a tick of the lag probe, so it is ordered by an event and not by a sleep.
"""

import enum
from contextlib import contextmanager
from typing import TYPE_CHECKING, override

import anyio
from attrs import frozen

from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.adapters.tracing.opentelemetry_tracer import OpenTelemetryTracer
from bookreviver.app.trace_report import load_spans
from bookreviver.domain.enums import SpanName, SpanParent
from tests.helpers.recording_tracer import RecordingTracer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from bookreviver.ports.tracing import AttributeValue, SpanAttributes, Tracer
    from tests.helpers.recording_tracer import RecordingSpan

SPAN_FILE_NAME: str = 'spans.jsonl'


class TracerAdapter(enum.StrEnum):
    """An adapter of the Tracer port under test."""

    NULL = 'null'
    RECORDING = 'recording'
    OPENTELEMETRY = 'opentelemetry'

    @property
    def keeps_spans(self) -> bool:
        """Whether the adapter keeps the spans it was given, which the null tracer by its nature does not."""
        return self is not TracerAdapter.NULL


@frozen(kw_only=True)
class FinishedSpan:
    """A span that ended, in the shape every adapter's reader gives.

    :ivar name: Name of the span.
    :ivar attributes: Its attributes by the text of their keys.
    :ivar parent: Name of the span that was current when it began, or None for a root.
    :ivar failed: Whether an exception left the block of the span.
    """

    name: str
    attributes: dict[str, AttributeValue]
    parent: str | None
    failed: bool


@frozen(kw_only=True)
class TracerUnderTest:
    """A tracer and the way to read back the spans that ended.

    :ivar tracer: The adapter under test.
    :ivar finished: Returns the spans that ended, in the order they ended; none for the null tracer.
    """

    tracer: Tracer
    finished: Callable[[], list[FinishedSpan]]


class AskingTracer(RecordingTracer):
    """A recording tracer that tells a test when a span of a name has started, so the test needs no sleep to order.

    A write block span starts right before the block joins the write queue, in the same turn of the event loop, so a
    test that waits for the event of ``ask_after`` knows the block is asking for the lock, and not that it has been
    given it. A tick of the lag probe starts as the previous one ends, so waiting for the second tick to start means
    the first has ended and carries its lag.
    """

    def __init__(self) -> None:
        """Start with no span awaited."""
        super().__init__()
        self._asked: anyio.Event | None = None
        self._awaited = SpanName.WRITE_BLOCK
        self._remaining = 0

    def ask_after(self, count: int, *, name: SpanName = SpanName.WRITE_BLOCK) -> anyio.Event:
        """Await the ``count``-th span of ``name`` that starts from now on.

        :param count: How many spans are to start, the awaited one the last of them.
        :type count: int
        :param name: Name of the spans that are counted.
        :type name: SpanName
        :returns: Event that is set when the awaited span has started.
        :rtype: anyio.Event
        """
        self._asked = anyio.Event()
        self._awaited = name
        self._remaining = count
        return self._asked

    @override
    def start(
        self,
        name: SpanName,
        attributes: SpanAttributes | None = None,
        *,
        parent: SpanParent = SpanParent.CURRENT,
    ) -> RecordingSpan:
        """Start the span as the recording tracer does, and set the event when it is the awaited one.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :param parent: The span that is current when this is called, or none for a root.
        :type parent: SpanParent
        :returns: The span, to end explicitly.
        :rtype: RecordingSpan
        """
        span = super().start(name, attributes, parent=parent)
        if name is self._awaited and self._remaining:
            self._remaining -= 1
            if not self._remaining and self._asked is not None:
                self._asked.set()
        return span


def read_span_file(path: Path) -> list[FinishedSpan]:
    """Read the JSON lines file the OpenTelemetry tracer wrote.

    :param path: The file of spans.
    :type path: Path
    :returns: The spans in the order of the file, which is the order they ended.
    :rtype: list[FinishedSpan]
    """
    spans = load_spans(path)
    names = {span.span_id: span.name for span in spans}
    return [
        FinishedSpan(
            name=span.name,
            attributes=dict(span.attributes),
            parent=None if span.parent_id is None else names.get(span.parent_id),
            failed=span.failed,
        )
        for span in spans
    ]


@contextmanager
def open_tracer_under_test(adapter: TracerAdapter, directory: Path) -> Iterator[TracerUnderTest]:
    """Set up an adapter with its reader; leaving the block flushes and closes what the adapter wrote.

    :param adapter: Adapter to set up.
    :type adapter: TracerAdapter
    :param directory: Directory the file adapter writes its file into.
    :type directory: Path
    :returns: Iterator yielding the tracer and the reader of its spans.
    :rtype: Iterator[TracerUnderTest]
    """
    match adapter:
        case TracerAdapter.NULL:
            yield TracerUnderTest(tracer=NullTracer(), finished=list)
        case TracerAdapter.RECORDING:
            recording = RecordingTracer()
            yield TracerUnderTest(
                tracer=recording,
                finished=lambda: [
                    FinishedSpan(
                        name=span.name.value,
                        attributes={key.value: value for key, value in span.attributes.items()},
                        parent=None if span.parent is None else span.parent.name.value,
                        failed=span.error is not None,
                    )
                    for span in recording.finished
                ],
            )
        case TracerAdapter.OPENTELEMETRY:
            path = directory / SPAN_FILE_NAME
            with OpenTelemetryTracer(path) as tracer:
                yield TracerUnderTest(tracer=tracer, finished=lambda: read_span_file(path))
