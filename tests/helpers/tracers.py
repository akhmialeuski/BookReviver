"""Every Tracer adapter set up with a reader of the spans it recorded, for the port contract and the adapter tests.

The null tracer keeps nothing, the recording tracer keeps its spans in memory, and the OpenTelemetry tracer writes them
into a file when it is closed, so the reader of each is a function that turns what the adapter holds into one shape,
``FinishedSpan``, which the contract compares across adapters. The file of the OpenTelemetry adapter is complete only
once its block is left, so a test reads the spans after the ``with`` block of ``open_tracer_under_test``.
"""

import enum
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.adapters.tracing.opentelemetry_tracer import OpenTelemetryTracer
from bookreviver.adapters.tracing.recording import RecordingTracer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from bookreviver.ports.tracing import AttributeValue, Tracer

SPAN_FILE_NAME: str = 'spans.jsonl'
ERROR_STATUS: str = 'ERROR'


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


def read_span_file(path: Path) -> list[FinishedSpan]:
    """Read the JSON lines file the OpenTelemetry tracer wrote.

    :param path: The file of spans.
    :type path: Path
    :returns: The spans in the order of the file, which is the order they ended.
    :rtype: list[FinishedSpan]
    """
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    names = {record['context']['span_id']: record['name'] for record in records}
    # The dictionary keeps the order of the file, so its identifiers run in step with the records
    return [
        FinishedSpan(
            name=names[span_id],
            attributes=record['attributes'],
            parent=names.get(record['parent_id']),
            failed=record['status']['status_code'] == ERROR_STATUS,
        )
        for span_id, record in zip(names, records, strict=True)
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
