"""A tracer that keeps its spans in memory, for the tests that check what a service or an adapter measured.

A test reads ``finished`` back, where each span holds its name, its attributes, the span that was current when it began
and its duration. The span that is current is tracked per task with a ``ContextVar``, as the SDK does, so spans opened
by two tasks of one test never become each other's children.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, override

from bookreviver.domain.spans import SpanParent
from bookreviver.ports.tracing import StartedSpan, Tracer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bookreviver.domain.spans import SpanAttribute, SpanName
    from bookreviver.ports.tracing import AttributeValue, Span, SpanAttributes

# The span a ``span`` block of any recording tracer is inside of; a tracer ignores one that another tracer opened
_CURRENT: ContextVar[RecordingSpan | None] = ContextVar('recording_tracer_current_span', default=None)


class RecordingSpan(StartedSpan):
    """A span of the recording tracer, whose facts a test reads once it has ended.

    :ivar tracer: The tracer that opened the span.
    :ivar name: What the span timed.
    :ivar attributes: Facts of the span, as set at the start and afterwards.
    :ivar parent: The span that was current when this one began, or None for a root.
    :ivar error: Name of the type of the exception that left the block of the span, or None.
    :ivar duration_ns: Nanoseconds from the start to the end, or None while the span is open.
    """

    def __init__(
        self,
        tracer: RecordingTracer,
        name: SpanName,
        attributes: SpanAttributes | None,
        parent: RecordingSpan | None,
    ) -> None:
        """Start the span now.

        :param tracer: The tracer that opened the span.
        :type tracer: RecordingTracer
        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :param parent: The span that was current when this one began.
        :type parent: RecordingSpan | None
        """
        self.tracer = tracer
        self.name = name
        self.attributes: dict[SpanAttribute, AttributeValue] = dict(attributes or {})
        self.parent = parent
        self.error: str | None = None
        self.duration_ns: int | None = None
        self._started_ns = time.perf_counter_ns()

    @override
    def set_attribute(self, key: SpanAttribute, value: AttributeValue) -> None:
        """Set an attribute of the span, replacing the value it had.

        :param key: The attribute.
        :type key: SpanAttribute
        :param value: What the attribute is for this span.
        :type value: AttributeValue
        """
        self.attributes[key] = value

    @override
    def end(self) -> None:
        """End the span and hand it to the tracer; ending it again changes nothing."""
        if self.duration_ns is None:
            self.duration_ns = time.perf_counter_ns() - self._started_ns
            self.tracer.finished.append(self)


class RecordingTracer(Tracer):
    """Keeps every span that ended, in the order they ended.

    :ivar finished: The ended spans, a child before its parent.
    """

    def __init__(self) -> None:
        """Start with no span recorded."""
        self.finished: list[RecordingSpan] = []

    def named(self, name: SpanName) -> list[RecordingSpan]:
        """Pick the ended spans of one name.

        :param name: What the spans timed.
        :type name: SpanName
        :returns: The spans, in the order they ended.
        :rtype: list[RecordingSpan]
        """
        return [span for span in self.finished if span.name is name]

    @override
    @contextmanager
    def span(
        self,
        name: SpanName,
        attributes: SpanAttributes | None = None,
        *,
        parent: SpanParent = SpanParent.CURRENT,
    ) -> Iterator[Span]:
        """Open a span that is current inside the block, and keep it when the block ends.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :param parent: The span that is current where the block is opened, or none for a root. The span of the block is
                       current inside it either way.
        :type parent: SpanParent
        :returns: Iterator yielding the span.
        :rtype: Iterator[Span]
        """
        span = self.start(name, attributes, parent=parent)
        token = _CURRENT.set(span)
        try:
            yield span
        except BaseException as error:
            span.error = type(error).__name__
            raise
        finally:
            _CURRENT.reset(token)
            span.end()

    @override
    def start(
        self,
        name: SpanName,
        attributes: SpanAttributes | None = None,
        *,
        parent: SpanParent = SpanParent.CURRENT,
    ) -> RecordingSpan:
        """Start a span that is not current, as a child of the span of this tracer that is current, if any.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :param parent: The span that is current when this is called, or none for a root.
        :type parent: SpanParent
        :returns: The span, to end explicitly.
        :rtype: RecordingSpan
        """
        current = _CURRENT.get()
        is_child = parent is SpanParent.CURRENT and current is not None and current.tracer is self
        return RecordingSpan(self, name, attributes, current if is_child else None)
