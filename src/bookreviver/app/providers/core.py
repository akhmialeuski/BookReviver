"""Providers of the settings and of the runtime services every feature shares."""

from collections.abc import Iterator

from dishka import Provider, Scope, from_context, provide

from bookreviver.adapters.events.broadcast import EventChannels
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.settings import Settings
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.runtime import BookEvents


class CoreProvider(Provider):
    """Settings, order keys and the event channels of the books, one of each per application."""

    scope = Scope.APP

    settings = from_context(provides=Settings)
    order_keys = provide(FractionalOrderKeys, provides=OrderKeys)

    @provide
    def event_channels(self, settings: Settings) -> Iterator[EventChannels]:
        """Build the in-process event channels, and register them as the way a book opens its channel.

        The registration is given back when the application closes the container, so the channels of another
        application stay registered.

        :param settings: Application settings, of which ``event_queue_size`` is read.
        :type settings: Settings
        :returns: Iterator yielding the channels of the books, which every book opens its own channel of.
        :rtype: Iterator[EventChannels]
        """
        channels = EventChannels(queue_size=settings.event_queue_size)
        previous = BookEvents.register(channels.open)
        try:
            yield channels
        finally:
            BookEvents.register(previous)
