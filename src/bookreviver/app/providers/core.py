"""Providers of the settings and of the runtime services every feature shares."""

from dishka import AnyOf, Provider, Scope, from_context, provide

from bookreviver.adapters.clock.system import SystemClock
from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.app.settings import Settings
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream


class CoreProvider(Provider):
    """Settings, clock and the event bus, one of each per application."""

    scope = Scope.APP

    settings = from_context(provides=Settings)
    clock = provide(SystemClock, provides=Clock)

    @provide
    def event_bus(self, settings: Settings) -> AnyOf[InProcessEventBus, EventPublisher, EventStream]:
        """Build the in-process event bus that serves both publishers and SSE subscribers."""
        return InProcessEventBus(queue_size=settings.event_queue_size)
