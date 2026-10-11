"""A tracer over the OpenTelemetry SDK that writes every span as one JSON object per line into a file.

The adapter owns one ``TracerProvider`` and never sets it as the global one, so nothing else in the process records into
it. The provider hands ended spans to a ``BatchSpanProcessor``, which queues them and exports from a thread of its own,
so a span that ends costs the event loop an append to a queue, not a write to the file. The exporter is the SDK's
``ConsoleSpanExporter`` pointed at the file, with ``ReadableSpan.to_json`` as its formatter.

The SDK flushes what is queued when the provider shuts down, so the file is complete only after ``close``, which the
``with`` block of the adapter calls. The file is opened for appending, so a run that is started again writes after the
spans of the earlier one, and a report of one run needs a file of its own.
"""

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING, ClassVar, Self, override

from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from bookreviver.ports.tracing import StartedSpan, Tracer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import TracebackType

    from opentelemetry.trace import Span as SdkSpan

    from bookreviver.domain.enums import SpanAttribute, SpanName
    from bookreviver.ports.tracing import AttributeValue, Span, SpanAttributes


def _keyed(attributes: SpanAttributes | None) -> dict[str, AttributeValue]:
    """Give the attributes the plain text keys the SDK takes.

    :param attributes: Facts of a span, or None.
    :type attributes: SpanAttributes | None
    :returns: The same facts under the values of their keys.
    :rtype: dict[str, AttributeValue]
    """
    return {key.value: value for key, value in (attributes or {}).items()}


class OpenTelemetrySpan(StartedSpan):
    """A span of the SDK, behind the port's two operations."""

    def __init__(self, span: SdkSpan) -> None:
        """Wrap ``span``.

        :param span: The span the SDK started.
        :type span: SdkSpan
        """
        self._span = span

    @override
    def set_attribute(self, key: SpanAttribute, value: AttributeValue) -> None:
        """Set an attribute of the span.

        :param key: The attribute.
        :type key: SpanAttribute
        :param value: What the attribute is for this span.
        :type value: AttributeValue
        """
        self._span.set_attribute(key.value, value)

    @override
    def end(self) -> None:
        """End the span, which hands it to the processor."""
        self._span.end()


class OpenTelemetryTracer(Tracer):
    """Writes the spans of the application to a JSON lines file through the OpenTelemetry SDK.

    :cvar SERVICE: The ``service.name`` resource attribute every span line carries.
    :cvar QUEUE_SIZE: Ended spans the processor holds before it drops the oldest, which a run of the end-to-end suite
                      does not come near, because the exporter thread empties the queue as it fills.
    """

    SERVICE: ClassVar[str] = 'bookreviver'
    QUEUE_SIZE: ClassVar[int] = 65_536

    def __init__(self, export_path: Path) -> None:
        """Open the file for appending and start the provider and its exporter thread.

        :param export_path: The file spans are written to, whose directory is created when it is missing.
        :type export_path: Path
        """
        export_path.parent.mkdir(parents=True, exist_ok=True)
        # The provider is shut down before the file closes, because the processor writes what it still holds on shutdown
        with ExitStack() as stack:
            file = stack.enter_context(export_path.open('a', encoding='utf-8'))
            exporter = ConsoleSpanExporter(out=file, formatter=lambda span: span.to_json(indent=None) + '\n')
            provider = TracerProvider(resource=Resource.create({SERVICE_NAME: self.SERVICE}))
            provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=self.QUEUE_SIZE))
            stack.callback(provider.shutdown)
            self._tracer = provider.get_tracer(__name__)
            self._stack = stack.pop_all()

    def __enter__(self) -> Self:
        """Return the tracer, which is already running.

        :returns: This tracer.
        :rtype: Self
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Flush the queued spans, stop the exporter thread and close the file.

        :param exc_type: Type of the exception that ends the block, or None.
        :type exc_type: type[BaseException] | None
        :param exc: The exception that ends the block, or None.
        :type exc: BaseException | None
        :param traceback: Traceback of that exception, or None.
        :type traceback: TracebackType | None
        """
        self._stack.close()

    @override
    @contextmanager
    def span(self, name: SpanName, attributes: SpanAttributes | None = None) -> Iterator[Span]:
        """Open a span that is current inside the block, as the SDK's ``start_as_current_span`` does.

        An exception that leaves the block is recorded on the span as an event, and sets its status to error.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :returns: Iterator yielding the span.
        :rtype: Iterator[Span]
        """
        with self._tracer.start_as_current_span(name.value, attributes=_keyed(attributes)) as span:
            yield OpenTelemetrySpan(span)

    @override
    def start(self, name: SpanName, attributes: SpanAttributes | None = None) -> StartedSpan:
        """Start a span that is not current, as a child of the span that is current.

        :param name: What the span times.
        :type name: SpanName
        :param attributes: Facts known when the work starts.
        :type attributes: SpanAttributes | None
        :returns: The span, to end explicitly.
        :rtype: StartedSpan
        """
        return OpenTelemetrySpan(self._tracer.start_span(name.value, attributes=_keyed(attributes)))
