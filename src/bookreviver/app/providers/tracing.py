"""Provider of the tracer: the OpenTelemetry one that writes a file when the measurements are on, else the null one.

The tracer is built once per application and given to everything that measures: the database, the worker, the runs of
stages and the HTTP middleware. Closing the container flushes the spans the exporter still queues and closes the file.
"""

from collections.abc import Iterator

from dishka import Provider, Scope, provide

from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.adapters.tracing.opentelemetry_tracer import OpenTelemetryTracer
from bookreviver.app.settings import Settings
from bookreviver.ports.tracing import Tracer


class TracingProvider(Provider):
    """One tracer per application."""

    @provide(scope=Scope.APP)
    def tracer(self, settings: Settings) -> Iterator[Tracer]:
        """Build the tracer the settings select, and shut it down with the application.

        :param settings: Application settings, of which the ``tracing`` group is read.
        :type settings: Settings
        :returns: Iterator yielding the tracer, which flushes and closes its file afterwards when it has one.
        :rtype: Iterator[Tracer]
        """
        if (sink := settings.tracing.sink) is None:
            yield NullTracer()
            return
        with OpenTelemetryTracer(sink) as tracer:
            yield tracer
