"""Providers of the settings and of the runtime services every feature shares."""

from dishka import AnyOf, Provider, Scope, from_context, provide

from bookreviver.adapters.clock.system import SystemClock
from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.settings import Settings
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream


class CoreProvider(Provider):
    """Settings, clock, order keys and the event bus, one of each per application."""

    scope = Scope.APP

    settings = from_context(provides=Settings)
    clock = provide(SystemClock, provides=Clock)
    order_keys = provide(FractionalOrderKeys, provides=OrderKeys)

    @provide
    def event_bus(self, settings: Settings) -> AnyOf[InProcessEventBus, EventPublisher, EventStream]:
        """Build the in-process event bus that serves both publishers and SSE subscribers.

        :param settings: Application settings, of which ``event_queue_size`` is read.
        :type settings: Settings
        :returns: One bus provided as itself, as the publisher port and as the stream port.
        :rtype: AnyOf[InProcessEventBus, EventPublisher, EventStream]
        """
        return InProcessEventBus(queue_size=settings.event_queue_size)
