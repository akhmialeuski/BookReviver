"""Providers of the settings and of the runtime services every feature shares."""

from dishka import Provider, Scope, from_context, provide

from bookreviver.adapters.events.broadcast import EventChannels
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.settings import Settings
from bookreviver.ports.ordering import OrderKeys


class CoreProvider(Provider):
    """Settings, order keys and the event channels of the books, one of each per application."""

    scope = Scope.APP

    settings = from_context(provides=Settings)
    order_keys = provide(FractionalOrderKeys, provides=OrderKeys)

    @provide
    def event_channels(self, settings: Settings) -> EventChannels:
        """Build the in-process event channels, which every book opens its own channel of.

        :param settings: Application settings, of which ``event_queue_size`` is read.
        :type settings: Settings
        :returns: The channels of the books.
        :rtype: EventChannels
        """
        return EventChannels(queue_size=settings.event_queue_size)
