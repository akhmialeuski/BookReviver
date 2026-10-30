"""The tracer of an application whose measurements are off, which does nothing and costs a call.

The block of ``span`` is the standard library's ``nullcontext`` over one shared span, which is reusable, so a stretch of
code that is measured when the measurements are on pays no allocation when they are off.
"""

from contextlib import nullcontext
from typing import TYPE_CHECKING, override

from bookreviver.domain.spans import SpanParent
from bookreviver.ports.tracing import StartedSpan, Tracer

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from bookreviver.domain.spans import SpanAttribute, SpanName
    from bookreviver.ports.tracing import AttributeValue, Span, SpanAttributes


class NullSpan(StartedSpan):
    """A span that holds no fact and has no end."""

    @override
    def set_attribute(self, key: SpanAttribute, value: AttributeValue) -> None:
        """Drop the attribute.

        :param key: The attribute.
        :type key: SpanAttribute
        :param value: What the attribute is for this span.
        :type value: AttributeValue
        """

    @override
    def end(self) -> None:
        """Do nothing."""


class NullTracer(Tracer):
    """Opens spans that record nothing."""

    def __init__(self) -> None:
        """Build the one span and the one block every call hands out."""
        self._span = NullSpan()
        self._block = nullcontext(self._span)

    @override
    def span(
        self,
        name: SpanName,
        attributes: SpanAttributes | None = None,
        *,
        parent: SpanParent = SpanParent.CURRENT,
    ) -> AbstractContextManager[Span]:
        """Return the shared block, which yields the shared span.

        :param name: What the span would time, ignored.
        :type name: SpanName
        :param attributes: Facts known when the work starts, ignored.
        :type attributes: SpanAttributes | None
        :param parent: Which span the new span would be the child of, ignored.
        :type parent: SpanParent
        :returns: A block that does nothing on entry and on exit.
        :rtype: AbstractContextManager[Span]
        """
        return self._block

    @override
    def start(
        self,
        name: SpanName,
        attributes: SpanAttributes | None = None,
        *,
        parent: SpanParent = SpanParent.CURRENT,
    ) -> StartedSpan:
        """Return the shared span.

        :param name: What the span would time, ignored.
        :type name: SpanName
        :param attributes: Facts known when the work starts, ignored.
        :type attributes: SpanAttributes | None
        :param parent: Which span the new span would be the child of, ignored.
        :type parent: SpanParent
        :returns: A span that holds nothing.
        :rtype: StartedSpan
        """
        return self._span
