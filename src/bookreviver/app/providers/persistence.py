"""Provider of the limit a catalogue database of the persistence port waits for the writer with, by its scope."""

from dishka import Provider, Scope, provide

# Neither exported by dishka's top level nor documented
from dishka.entities.marker import HasContext
from starlette.requests import Request

from bookreviver.app.settings import Settings
from bookreviver.ports.persistence import ChangeWaitSeconds


class ChangeWaitProvider(Provider):
    """The limit a catalogue database waits for the write lock with, chosen by the context of the scope it lives in.

    A catalogue database that serves an HTTP request must answer in seconds, so its limit is short and a busy book is
    reported at once as a problem the interface can retry. A background job must not lose its work to a short wait,
    and the work of the startup in the lifespan is no request either, so both get the long limit. Neither is told
    apart by a flag that every caller would have to pass. dishka's FastAPI integration puts the ``Request`` into the
    context of the scope of every HTTP request, a job opens its scope with no ``Request``, and the lifespan puts
    nothing, so the presence of the ``Request`` in the context is what makes a scope a request, and every other scope
    falls back to the default factory.
    """

    @provide(scope=Scope.REQUEST)
    def job_limit(self, settings: Settings) -> ChangeWaitSeconds:
        """Give the limit of a catalogue database that serves no HTTP request.

        :param settings: Application settings, of which the limit of a job is read.
        :type settings: Settings
        :returns: The long limit, for a background job and the work of the startup.
        :rtype: ChangeWaitSeconds
        """
        return ChangeWaitSeconds(settings.change_wait.job_seconds)

    # Registered after the default, so that dishka takes it in a scope whose context has a Request
    @provide(scope=Scope.REQUEST, when=HasContext(Request))
    def request_limit(self, settings: Settings) -> ChangeWaitSeconds:
        """Give the limit of a catalogue database that serves an HTTP request.

        :param settings: Application settings, of which the limit of a request is read.
        :type settings: Settings
        :returns: The short limit, so a request never hangs for the wait of a job.
        :rtype: ChangeWaitSeconds
        """
        return ChangeWaitSeconds(settings.change_wait.request_seconds)
