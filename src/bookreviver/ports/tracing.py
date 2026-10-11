"""Tracing port: timed spans around the work whose duration the application is asked to report.

A service or an adapter that wants a stretch of its work measured opens a span through ``Tracer`` and never imports the
library that records it, so the library, the file it writes to and whether anything is recorded at all are decided in
``app``. The names of spans and the keys of their attributes are the closed sets ``SpanName`` and ``SpanAttribute``, so
the report of a run groups by values that exist.

Two operations match the two ways a span is needed. ``span`` is a ``with`` block: the span is current inside it, so the
spans opened there become its children, and it ends when the block ends, with the exception that leaves it recorded.
``start`` is for work that has no ``with`` around it, such as the callbacks of an engine that begin and end a
transaction in two places: the span is not made current, and its owner ends it.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager

    from bookreviver.domain.enums import SpanAttribute, SpanName

# What a span can carry: a text, a number or a flag. An identifier is given as its text.
type AttributeValue = str | int | float | bool
type SpanAttributes = Mapping[SpanAttribute, AttributeValue]


class Span(ABC):
    """A span that is open, which a fact learnt while the work runs is added to."""

    @abstractmethod
    def set_attribute(self, key: SpanAttribute, value: AttributeValue) -> None:
        """Set an attribute of the span, replacing the value it had.

        :param key: The attribute.
        :type key: SpanAttribute
        :param value: What the attribute is for this span.
        :type value: AttributeValue
        """


class StartedSpan(Span):
    """A span that its owner ends, which ``Tracer.start`` returns."""

    @abstractmethod
    def end(self) -> None:
        """End the span, once; its duration is the time since it was started."""


class Tracer(ABC):
    """Records the spans of work the application is asked to measure."""

    @abstractmethod
    def span(self, name: SpanName, attributes: SpanAttributes | None = None) -> AbstractContextManager[Span]:
        """Open a span for the block, which is current while the block runs.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :returns: Context manager that yields the span, and ends it when the block ends. An exception that leaves the
                  block is recorded on the span and goes on.
        :rtype: AbstractContextManager[Span]
        """

    @abstractmethod
    def start(self, name: SpanName, attributes: SpanAttributes | None = None) -> StartedSpan:
        """Start a span that is not current and that the caller ends.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :returns: The span, which is the child of the span that is current when this is called.
        :rtype: StartedSpan
        """
